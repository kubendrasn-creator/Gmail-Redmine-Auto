from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser


def main():
    gmail = GmailClient()
    parser = EmailParser()

    messages = gmail.get_messages(max_results=5)

    print(f"Messages found: {len(messages)}")

    for message in messages:
        raw_email = gmail.get_message(message["id"])

        email = parser.parse(raw_email)

        print("\n" + "=" * 60)
        print(f"Message ID   : {email['message_id']}")
        print(f"Thread ID    : {email['thread_id']}")
        print(f"Sender Name  : {email['sender_name']}")
        print(f"Sender Email : {email['sender_email']}")
        print(f"Subject      : {email['subject']}")
        print(f"Received At  : {email['received_at']}")
        print(f"Body:\n{email['body']}")


if __name__ == "__main__":
    main()