from asyncio import run
from base64 import b64encode
from os import urandom, environ
from os.path import exists

from motor.motor_asyncio import AsyncIOMotorClient

if exists(".env"):
    from dotenv import load_dotenv

    load_dotenv()


async def main():
    mongo = AsyncIOMotorClient(environ.get("MONGODB")).s3
    print("Enter username (or press enter) to create access keys, or press Ctrl+C to exit.\n")
    while True:
        try:
            name = input("Username: ")
        except KeyboardInterrupt:
            break
        if not name:
            name = None
        key_id = urandom(12).hex()
        key = b64encode(urandom(24)).decode("utf8").replace("=", "").replace("/", "_")
        await mongo.users.insert_one({"name": name, "id": key_id, "key": key})
        print(f"  Key Id: {key_id}\n  Access Key: {key}\n")


if __name__ == "__main__":
    run(main())
