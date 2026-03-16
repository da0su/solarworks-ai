from datetime import datetime

def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("===================================")
    print("Solar Works - Hello Bot")
    print("CEO command received successfully.")
    print(f"Executed at: {now}")
    print("Status: BOT is running normally.")
    print("===================================")

if __name__ == "__main__":
    main()