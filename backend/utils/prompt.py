import sys


def ask_yes_no(prompt_text, default=None):
    """
    Prompt user for a Yes/No answer in the terminal.

    Requirements:
    - Accepts Y, y, Yes, yes.
    - Accepts N, n, No, no.
    - Reprompts on invalid input.
    - Does not default to Yes (default=None by default).
    - Safely handles Ctrl+C (KeyboardInterrupt) by exiting cleanly.
    """
    if default is True:
        hint = "[Y/n]"
    elif default is False:
        hint = "[y/N]"
    else:
        hint = "[Y/N]"

    while True:
        try:
            formatted_prompt = f"{prompt_text.strip()} {hint}: "
            response = input(formatted_prompt).strip()
        except KeyboardInterrupt:
            print("\nOperation cancelled by user (Ctrl+C). Exiting safely.")
            sys.exit(0)
        except EOFError:
            print("\nInput stream closed. Exiting safely.")
            sys.exit(0)

        if not response and default is not None:
            return default

        lower = response.lower()
        if lower in ("y", "yes"):
            return True
        elif lower in ("n", "no"):
            return False
        else:
            print("Invalid input. Please enter 'Y' for Yes or 'N' for No.")
