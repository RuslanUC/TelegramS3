# Telegram S3

Use telegram as s3 storage.

<details>
<summary><b>Enviroment variables you need to set:</b></summary>

`API_ID`: Go to [my.telegram.org](https://my.telegram.org) to obtain this.

`API_HASH`: Go to [my.telegram.org](https://my.telegram.org) to obtain this.

`BOT_TOKEN`: Get the bot token from [BotFather](https://telegram.dog/botfather).

`MONGODB`: MongoDB connect string.

`CHAT_ID`: Chat id to send files to.

</details>

<details>
<summary><b>Setup:</b></summary>

  1. Create bot in [BotFather](https://telegram.dog/botfather).
  2. Obtain API_ID and API_HASH on [my.telegram.org](https://my.telegram.org).
  3. Create mongodb database on [MongoDB Cloud](https://cloud.mongodb.com/) (or use your server) and copy connect string.
  4. Insert all variables into .env
  5. Add bot to your channel with admin rights.
  6. Run `get_channel_id.py`, send `/id` command in your channel.
  7. Copy id to .env
  8. Create mongodb database named `s3`.
  9. Run `setup_collections.py`.
  10. Run `create_accounts.py` to create access keys.
  11. Run `main.py`.

</details>

<details>
<summary><b>How to use:</b></summary>

```python
import boto3

s3 = boto3.client("s3",
	endpoint_url = "your url", # http://127.0.0.1:8000
	aws_access_key_id = "your access id",
	aws_secret_access_key = "your secret key"
)

s3.create_bucket(Bucket="test_bucket") # create bucket
s3.upload_file("path/to/file", "test_bucket", "file") # upload local file
s3.download_file("test_bucket", "file", "local_file") # download s3 file
```

</details>