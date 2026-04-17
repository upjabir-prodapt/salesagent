import os
import subprocess
import sys


def main():
    """Runs the Streamlit UI."""
    print("Starting Streamlit UI for Colt-AI...")
    ui_path = os.path.join(os.path.dirname(__file__), "ui", "app.py")

    if not os.path.exists(ui_path):
        print(f"Error: Could not find Streamlit entrypoint at {ui_path}")
        sys.exit(1)

    try:
        subprocess.run(["streamlit", "run", ui_path], check=True)
    except KeyboardInterrupt:
        print("\nStreamlit UI stopped.")
    except Exception as e:
        print(f"Error running Streamlit: {e}")


if __name__ == "__main__":
    main()
