from asyncio import get_running_loop
from io import BytesIO, SEEK_END
from time import time
from typing import TypedDict, Callable, Awaitable

from magic import from_buffer
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client
from pyrogram.types import Message
from pys3server import BaseInterface, S3Object, Part, BaseWriteStream, Bucket, BaseReadStream, AccessDenied, \
    BucketAlreadyExists, BucketAlreadyOwnedByYou, NoSuchKey

from tg import File


class ObjectPart(TypedDict):
    part_id: int
    tg_file: str
    tg_message: int
    size: int | None


class TelegramReadStream(BaseReadStream):
    def __init__(self, bot: Client, parts: list[ObjectPart], size: int):
        parts.sort(key=lambda x: x["part_id"])
        self._range = all([part.get("size") is not None for part in parts])
        self._files = [File(part["tg_file"], bot) for part in parts]
        self._size = size

    async def read(self) -> bytes | None:
        if not self._files:
            return None

        while not (data := await self._files[0].read()) and len(self._files) > 0:
            self._files.pop(0)

        return data if data else None

    async def supports_range(self) -> bool:
        return self._range

    async def total_size(self) -> int | None:
        return self._size


class TelegramWriteStream(BaseWriteStream):
    DOCUMENT_SIZE = 1024 * 1024 * 8

    def __init__(
            self, bot: Client, chat_id: str, part: int,
            upload_callback: Callable[[Message, int, str | None], Awaitable[None]]
    ):
        self._bot = bot
        self._chat_id = chat_id
        self._part = part
        self._calc_mime = part <= 1
        self._upload_cb = upload_callback
        self._buffer = BytesIO()
        setattr(self._buffer, "name", "file")

    async def _upload(self) -> None:
        self._buffer.seek(0, SEEK_END)
        size = self._buffer.tell()
        if not size:
            return
        mime = None
        if self._calc_mime:
            self._buffer.seek(0)
            mime = from_buffer(self._buffer.read(1024), mime=True)
            self._buffer.seek(0)

        message = await self._bot.send_document(self._chat_id, self._buffer, file_name="file", force_document=True)
        await self._upload_cb(message, size, mime)

    async def write(self, content: bytes | None) -> None:
        if content is not None:
            self._buffer.write(content)

        if content is None or self._buffer.tell() > self.DOCUMENT_SIZE:
            await self._upload()
            self._buffer = BytesIO()
            setattr(self._buffer, "name", "file")


class TelegramInterface(BaseInterface):
    def __init__(self, api_id: int, api_hash: str, bot_token: str, chat_id: str, mongo_url: str):
        self._chat_id = chat_id
        self._bot = Client(
            "S3Bot",
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            in_memory=True,
        )

        self._mongo_url = mongo_url
        self._mongo: AsyncIOMotorClient | None = None

        self._allow_public = False

    async def on_start(self) -> None:
        await self._bot.start()
        self._mongo = AsyncIOMotorClient(self._mongo_url, io_loop=get_running_loop()).s3

    async def access_key(self, key_id: str | None, object_: S3Object | Bucket | None) -> str | None:
        creds = {"key": None}
        if (key_id is None or not (creds := await self._mongo.users.find_one({"id": key_id}))) \
                and not self._allow_public:
            bucket = object_.bucket if isinstance(object_, S3Object) else object_
            raise AccessDenied(bucket, object_)

        return creds["key"]

    async def create_bucket(self, key_id: str, bucket_name: str) -> Bucket:
        bucket = await self._mongo.buckets.find_one({"name": bucket_name})
        bucket_ = Bucket(bucket_name)
        if bucket:
            if bucket["owner"] == key_id:
                raise BucketAlreadyOwnedByYou(bucket_)
            else:
                raise BucketAlreadyExists(bucket_)

        await self._mongo.buckets.insert_one({
            "name": bucket_name,
            "owner": key_id,
            "time": int(time()),
            "public": True
        })

        return bucket_

    async def list_buckets(self, key_id: str) -> list[Bucket]:
        buckets = []
        async for bucket in self._mongo.buckets.find({"owner": key_id}):
            buckets.append(Bucket(bucket["name"], bucket["time"]))

        return buckets

    async def list_bucket(self, key_id: str, bucket: Bucket) -> list[S3Object]:
        if not await self._mongo.buckets.find_one({"name": bucket, "owner": key_id}):
            raise NoSuchKey(bucket)

        objects = []
        query = {"owner": key_id, "bucket": bucket.name, "incomplete": {"$exists": False}}
        async for obj in self._mongo.objects.find(query).limit(1000):
            objects.append(S3Object(bucket, obj["name"], obj["size"]))

        return objects

    async def read_object(
            self, key_id: str, object_: S3Object, content_range: tuple[int, int] | None = None
    ) -> TelegramReadStream:
        bucket = object_.bucket

        if not (b := await self._mongo.buckets.find_one({"name": bucket.name})):
            raise NoSuchKey(bucket, object_)
        if not b["public"] and b["owner"] != key_id:
            raise AccessDenied(bucket, object_)
        query = {"bucket": bucket, "name": object_.name, "incomplete": {"$exists": False}}
        if not (obj := await self._mongo.objects.find_one(query)):
            raise NoSuchKey(bucket, object_)

        return TelegramReadStream(self._bot, obj["parts"], obj["size"])

    async def write_object(self, key_id: str, bucket: Bucket, object_name: str, size: int) -> TelegramWriteStream:
        async def upload_cb(message: Message, uploaded_size: int, mime_type: str | None) -> None:
            if await self._mongo.objects.find_one({"name": object_name, "bucket": bucket}):
                await self._mongo.objects.delete_one({"name": object_name, "bucket": bucket})
            await self._mongo.objects.insert_one({
                "name": object_name,
                "owner": key_id,
                "time": int(time()),
                "size": uploaded_size,
                "mime_type": mime_type,
                "bucket": bucket,
                #"hash": md5_checksum,  # TODO: calculate md5
                "parts": [{
                    "part_id": 0,
                    "tg_file": message.document.file_id,
                    "tg_message": message.id,
                    "size": uploaded_size,
                }]
            })

        return TelegramWriteStream(self._bot, self._chat_id, 0, upload_cb)

    async def create_multipart_upload(self, key_id: str, bucket: Bucket, object_name: str) -> S3Object:
        await self._mongo.buckets.delete_one({"bucket": bucket.name, "name": object_name, "owner": key_id})
        await self._mongo.objects.insert_one({
            "name": object_name,
            "owner": key_id,
            "time": int(time()),
            "size": 0,
            "mime_type": None,
            "bucket": bucket.name,
            "hash": None,
            "parts": [],
            "incomplete": True,
        })

        return S3Object(bucket, object_name, 0)

    async def write_object_multipart(self, object_: S3Object, part_id: int, size: int) -> TelegramWriteStream:
        async def upload_cb(message: Message, uploaded_size: int, mime_type: str | None) -> None:
            payload = {
                "$inc": {
                    "size": uploaded_size,
                },
                "$push": {
                    "parts": {
                        "part_id": part_id,
                        "tg_file": message.document.file_id,
                        "tg_message": message.id,
                        "size": uploaded_size,
                    }
                }
            }
            if mime_type is not None:
                payload["$set"] = {"mime_type": mime_type}
            await self._mongo.objects.update_one(
                {"bucket": object_.bucket.name, "name": object_.name, "incomplete": True},
                payload
            )

        return TelegramWriteStream(self._bot, self._chat_id, part_id, upload_cb)

    async def finish_multipart_upload(self, object_: S3Object, parts: list[Part]) -> None:
        await self._mongo.objects.update_one(
            {"bucket": object_.bucket.name, "name": object_.name, "incomplete": True},
            {"$unset": {"incomplete": 1}}
        )

    async def delete_object(self, key_id: str, object_: S3Object) -> None:
        bucket = object_.bucket
        query = {"bucket": bucket.name, "name": object_.name, "owner": key_id}
        if not (obj := await self._mongo.objects.find_one(query)):
            return

        await self._mongo.buckets.delete_one({"bucket": bucket.name, "name": object_.name, "owner": key_id})
        message_ids = [part["tg_message"] for part in obj["parts"]]
        await self._bot.delete_messages(self._chat_id, message_ids)

    async def delete_bucket(self, key_id: str, bucket: Bucket) -> None:
        await self._mongo.buckets.delete_one({"name": bucket.name, "owner": key_id})
