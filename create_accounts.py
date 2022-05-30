from motor.motor_asyncio import AsyncIOMotorClient
from asyncio import run
from os import urandom, environ
from base64 import b64encode
from os.path import exists
if exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()

async def main():
	mongo = AsyncIOMotorClient(environ.get("MONGODB")).s3
	print("Enter username (or press enter) to create access keys, or press Ctrl+C to exit.\n")
	while True:
		try:
			name = input("Username: ")
		except:
			break
		if not name:
			name = None
		id = urandom(12).hex()
		key = b64encode(urandom(24)).decode("utf8").replace("=", "").replace("/", "_")
		await mongo.users.insert_one({"name": name, "id": id, "key": key})
		print(f"Id: {id}\nKey: {key}\n")

if __name__ == "__main__":
	run(main())