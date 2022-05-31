from quart import Quart, request
from pyrogram import Client
from s3 import *
from motor.motor_asyncio import AsyncIOMotorClient
from asyncio import get_event_loop
from auth import SignatureV4
from os import environ
from os.path import exists
from functools import wraps
from datetime import datetime
from time import time
from re import compile
from utils import patch_binaryio
from tg import stream_file
from base64 import b64decode
from uuid import uuid4
from hashlib import md5
from io import BytesIO
from magic import from_buffer
if exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()
import sys

bucket_name_pattern = compile('^[a-z0-9_-]{1,255}$')
etag_pattern = compile(r'(?:<ETag>")([a-z\d]{32})(?:"<\/ETag>)')
partnum_pattern = compile(r'(?:<PartNumber>)(\d{1,})(?:<\/PartNumber>)')
access_pattern = compile(r'(?:<BlockPublicAcls>)(true|false)(?:<\/BlockPublicAcls>)')

app = Quart("Telegram_S3")
app.url_map.strict_slashes = False
app.config['MAX_CONTENT_LENGTH'] = 512 * 1024 * 1024
app.config["RESPONSE_TIMEOUT"] = 9000
app.config["BODY_TIMEOUT"] = 600

@app.before_serving
async def startup():
    global bot, mongo
    bot = Client(
        "S3_Bot",
        api_id=int(environ.get("API_ID", 0)),
        api_hash=environ.get("API_HASH"),
        bot_token=environ.get("BOT_TOKEN"),
        in_memory=True
    )
    await bot.start()
    loop = get_event_loop()
    mongo = AsyncIOMotorClient(environ.get("MONGODB"), io_loop=loop).s3

def auth(allow_public=False):
    def _auth(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            a = SignatureV4(request)
            if not a.userId and not allow_public:
                return InvalidAccessKeyId
            if not (u := await mongo.users.find_one({"id": a.userId})) and not allow_public:
                return InvalidAccessKeyId
            if u and not a.verify(u["key"]) and not allow_public:
                return SignatureDoesNotMatch
            if a.verified:
                kwargs["user"] = User(a.userId, u.get("name"))
            return await f(*args, **kwargs)
        return wrapped
    return _auth

def _lower(args):
    def __lower(f):
        @wraps(f)
        async def wrapped(*args, **kwargs):
            for arg in args:
                if (a := kwargs.get(arg)):
                    kwargs[arg] = a.lower()
            return await f(*args, **kwargs)
        return wrapped
    return __lower

@app.route("/", methods=["GET"])
@auth()
async def listBuckets(user):
    buckets = []
    async for bucket in mongo.buckets.find({"owner": user.id}):
        buckets.append(Bucket(bucket["name"], datetime.utcfromtimestamp(bucket["time"]).strftime("%Y-%m-%dT%H:%M:%SZ")))
    return ListAllMyBucketsResult(Owner(user.id, user.name), buckets).gen()

@app.route("/<string:bucket>", methods=["DELETE"])
@auth()
@_lower(["bucket"])
async def deleteBucket(bucket, user):
    if not await mongo.buckets.find_one({"name": bucket, "owner": user.id}):
        return NoSuchBucket
    await mongo.buckets.delete_one({"name": bucket, "owner": user.id})
    return "", 204

@app.route("/<string:bucket>", methods=["GET"])
@auth()
@_lower(["bucket"])
async def bucketData(bucket, user):
    if not await mongo.buckets.find_one({"name": bucket, "owner": user.id}):
        return NoSuchBucket
    if "location" in request.args:
        return LocationConstraint().gen()
    elif "versioning" in request.args:
        return VersioningConfiguration().gen()
    objects = []
    ow = Owner(user.id, user.name)
    async for obj in mongo.objects.find({"owner": user.id, "bucket": bucket, "incomplete": {"$exists": False}}).limit(1000):
        objects.append(Contents(obj["name"], obj["hash"], ow, obj["size"], obj["modified"]))
    return ListBucketResult(objects, bucket)

@app.route("/<string:bucket>", methods=["PUT"])
@auth()
@_lower(["bucket"])
async def putBucket(bucket, user):
    b = await mongo.buckets.find_one({"name": bucket})
    if "publicAccessBlock" in request.args:
        if not b or b["owner"] != user.id:
            return NoSuchBucket
        acc = access_pattern.findall((await request.body).decode("utf8"))
        if acc:
            public = acc[0].lower() == "false"
            await mongo.buckets.update_one({"name": bucket, "owner": user.id}, {"$set": {"public": public}})
    else:
        if not bucket_name_pattern.match(bucket):
            return InvalidBucketName
        if b:
            return BucketAlreadyExists
        await mongo.buckets.insert_one({"name": bucket, "owner": user.id, "time": round(time()), "public": True})
    return ""

@app.route("/<string:bucket>/<path:file>", methods=["DELETE"])
@auth()
@_lower(["bucket"])
async def deleteObject(bucket, file, user):
    if not (r := await mongo.objects.find_one({"bucket": bucket, "name": file, "owner": user.id})):
        return "", 204
    await mongo.buckets.delete_one({"bucket": bucket, "name": file, "owner": user.id})
    messages = [p["tg_message"] for p in r["parts"]]
    await bot.delete_messages(int(environ.get("CHAT_ID")), messages)
    return "", 204

async def putObjectSinglepart(request, bucket, file, user):
    b = await request.body
    data = BytesIO(b)
    h = True
    if not (md5_checksum := request.content_md5):
        m, h = patch_binaryio(data), False
    msg = await bot.send_document(int(environ.get("CHAT_ID")), data, file_name="file", force_document=True)
    if not h:
        md5_checksum = m.hexdigest()
    else:
        md5_checksum = b64decode(bytes(md5_checksum, "utf8")).hex()
    if await mongo.objects.find_one({"name": file, "bucket": bucket}):
        await mongo.objects.delete_one({"name": file, "bucket": bucket})
    await mongo.objects.insert_one({
        "name": file,
        "owner": user.id,
        "time": round(time()),
        "size": len(b),
        "mime_type": from_buffer(b, mime=True),
        "bucket": bucket,
        "hash": md5_checksum,
        "parts": [{
            "part_id": 0,
            "tg_file": msg.document.file_id,
            "tg_message": msg.id
        }]
    })
    return "", 200, {"etag": f"\"{md5_checksum}\""}

async def putObjectMultipart(request, bucket, file, user, uploadId, partNumber):
    b = await request.body
    data = BytesIO(b)
    h = True
    if not (md5_checksum := request.content_md5):
        m, h = patch_binaryio(data), False
    msg = await bot.send_document(int(environ.get("CHAT_ID")), data, file_name="file", force_document=True)
    if not h:
        md5_checksum = m.hexdigest()
    else:
        md5_checksum = b64decode(bytes(md5_checksum, "utf8")).hex()
    payload = {"$inc": {"size": len(b)}, "$push": {"parts": {"part_id": partNumber, "tg_file": msg.document.file_id, "tg_message": msg.id}}}
    if partNumber == 1:
        m = from_buffer(b, mime=True)
        payload["$set"] = {"mime_type": m}
    await mongo.objects.update_one({"uploadId": uploadId}, payload)
    return "", 200, {"etag": f"\"{md5_checksum}\""}

@app.route("/<string:bucket>/<path:file>", methods=["PUT"])
@auth()
@_lower(["bucket"])
async def putObject(bucket, file, user):
    b = await mongo.buckets.find_one({"name": bucket, "owner": user.id})
    if not b:
        return NoSuchBucket
    if (uploadId := request.args.get("uploadId")):
        partNumber = request.args.get("partNumber", 0)
        return await putObjectMultipart(request, bucket, file, user, uploadId, int(partNumber))
    else:
        return await putObjectSinglepart(request, bucket, file, user)
    return ""

@app.route("/<string:bucket>/<path:file>", methods=["POST"])
@auth()
@_lower(["bucket"])
async def createMultipartUpload(bucket, file, user):
    user = User("0042262b378a7f50000000001", "Test")
    if "uploads" in request.args:
        await mongo.buckets.delete_one({"bucket": bucket, "name": file, "owner": user.id})
        uploadId = str(uuid4())
        await mongo.objects.insert_one({
            "name": file,
            "owner": user.id,
            "time": round(time()),
            "size": 0,
            "mime_type": None,
            "bucket": bucket,
            "hash": None,
            "parts": [],
            "incomplete": True,
            "uploadId": uploadId
        })
        return InitiateMultipartUploadResult(bucket, file, uploadId).gen()
    elif (uploadId := request.args.get("uploadId")):
        b = await request.body
        b = b.decode("utf8")
        parts = list(zip(etag_pattern.findall(b), partnum_pattern.findall(b)))
        parts.sort(key=lambda x: int(x[1]))
        parts = [m[0] for m in parts]
        b = b""
        for part in parts:
            b += bytes.fromhex(part)
        m = md5()
        m.update(b)
        hash = m.hexdigest()+f"-{len(parts)}"
        await mongo.objects.update_one({"uploadId": uploadId}, {"$set": {"hash": hash}, "$unset": {"incomplete": 1, "uploadId": 1}})
        return CompleteMultipartUploadResult(bucket, file, hash).gen()

@app.route("/<string:bucket>/<path:file>", methods=["GET", "HEAD"])
@auth(True)
@_lower(["bucket"])
async def getObject(bucket, file, user=None):
    if not user:
        user = User(None, None)
    if not (b := await mongo.buckets.find_one({"name": bucket})):
        return NoSuchBucket
    if not b["public"] and b["owner"] != user.id:
        return Error("Forbidden", f"You dont have access to bucket \"{bucket}\"").gen(), 403
    if not (r := await mongo.objects.find_one({"bucket": bucket, "name": file, "incomplete": {"$exists": False}})):
        return "", 404
    if request.method == "HEAD":
        return "", 200, {"Content-Length": r["size"]}
    mime = r["mime_type"]
    headers = {"Content-Type": mime or "application/octet-stream"}
    if not (mime.startswith("image/") or mime.startswith("text/")):
        name = r["name"].split("/")[-1]
        headers["Content-Disposition"] = f"attachment; filename={name}"
    return stream_file(r["parts"], bot), 200, headers

@app.route("/healthcheck")
def hc():
    return ""

if __name__ == "__main__":
    from uvicorn import run as urun
    urun('main:app', host="0.0.0.0", port=8000, reload=True, use_colors=False)