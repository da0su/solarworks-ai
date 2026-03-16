def analyze_command(text):

    text = text.lower()

    if "create bot" in text:
        return "CREATE_BOT"

    elif "run bot" in text:
        return "RUN_BOT"

    elif "status" in text:
        return "STATUS"

    else:
        return "UNKNOWN"