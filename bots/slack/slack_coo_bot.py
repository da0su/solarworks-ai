import os
import subprocess
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN")

app = App(token=SLACK_BOT_TOKEN)


@app.event("app_mention")
def handle_app_mention(body, say, logger):

    event = body.get("event", {})
    user = event.get("user", "unknown")
    text = event.get("text", "").lower()
    channel = event.get("channel", "unknown")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    log_line = f"[{now}] user={user} channel={channel} command={text}\n"

    with open("command_log.txt", "a", encoding="utf-8") as f:
        f.write(log_line)

    print(log_line.strip())

    # ===== COO COMMAND ENGINE =====

    if "hello" in text:
        say("Solar Works COO: Hello CEO")

    elif "status" in text:
        say("Solar Works COO: 全システム正常")

    elif "run hello bot" in text:
        subprocess.run(["python", "hello_bot.py"])
        say("Solar Works COO: hello_bot 実行しました")

    else:
        say("Solar Works COO: コマンドを理解できません")


if __name__ == "__main__":

    print("Solar Works Slack COO BOT is running...")

    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()