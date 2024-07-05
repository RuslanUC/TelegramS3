from asyncio import run
from os import environ
from os.path import exists

from motor.motor_asyncio import AsyncIOMotorClient

if exists(".env"):
    from dotenv import load_dotenv

    load_dotenv()


async def main():
    mongo = AsyncIOMotorClient(environ.get("MONGODB")).s3
    for collection in ["users", "buckets", "objects"]:
        try:
            await mongo.create_collection(collection)
        except Exception as e:
            print(f"Error while creating collection: {e.__class__.__name__}: {e}")


if __name__ == "__main__":
    run(main())
