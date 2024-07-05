from pyrogram import Client
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId
from pyrogram.raw.functions.auth import ImportAuthorization, ExportAuthorization
from pyrogram.raw.functions.upload import GetFile
from pyrogram.raw.types import InputDocumentFileLocation
from pyrogram.session import Session, Auth


class File:
    BS = 1024 * 1024

    def __init__(self, id_: str, client: Client):
        self.id = FileId.decode(id_)
        self.client = client

        self._session: Session | None = None
        self._location: InputDocumentFileLocation | None = None
        self._offset = 0
        self._eof = False

    async def read(self) -> bytes:
        if self._eof:
            return b""

        if self._session is None:
            self._session = await get_media_session(self.client, self.id)
        if self._location is None:
            self._location = InputDocumentFileLocation(
                id=self.id.media_id,
                access_hash=self.id.access_hash,
                file_reference=self.id.file_reference,
                thumb_size=self.id.thumbnail_size
            )

        resp = await self._session.send(GetFile(location=self._location, offset=self._offset, limit=self.BS))
        data = resp.bytes
        self._offset += len(data)

        if len(data) != self.BS:
            self._eof = True

        return data


async def get_media_session(client: Client, file_id) -> Session:
    if (media_session := client.media_sessions.get(file_id.dc_id)) is not None:
        return media_session

    if file_id.dc_id != await client.storage.dc_id():
        media_session = Session(
            client, file_id.dc_id, await Auth(client, file_id.dc_id, await client.storage.test_mode()).create(),
            await client.storage.test_mode(), is_media=True
        )
        await media_session.start()

        for _ in range(6):
            exported_auth = await client.invoke(ExportAuthorization(dc_id=file_id.dc_id))
            try:
                await media_session.invoke(ImportAuthorization(id=exported_auth.id, bytes=exported_auth.bytes))
                break
            except AuthBytesInvalid:
                continue
        else:
            await media_session.stop()
            raise AuthBytesInvalid
    else:
        media_session = Session(
            client, file_id.dc_id, await client.storage.auth_key(), await client.storage.test_mode(), is_media=True
        )
        await media_session.start()

    client.media_sessions[file_id.dc_id] = media_session
    return media_session
