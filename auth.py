"""
auth.py — simple username/password gate.

Credentials live in Streamlit secrets, NEVER in code or a committed file:
  - Local dev:  .streamlit/secrets.toml  (gitignored)
  - Streamlit Cloud: Settings -> Secrets, in the app dashboard (encrypted
    at rest, never visible in your repo)

Password is compared as a SHA-256 hash, not plaintext — so even if
someone got read access to your secrets store, they'd get a hash, not
the actual password. Generate your own hash with:
    python -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"

This is a basic gate (single shared username/password), appropriate for
a single-user personal portfolio tool — NOT a substitute for proper
multi-user auth if you ever share this app with others.
"""

import hashlib
import streamlit as st


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def check_password() -> bool:
    """
    Returns True if the user is authenticated (and renders nothing).
    Returns False and renders a login form if not yet authenticated —
    caller should st.stop() immediately after a False return.
    """
    if st.session_state.get("authenticated", False):
        return True

    try:
        expected_username = st.secrets["auth"]["username"]
        expected_password_hash = st.secrets["auth"]["password_hash"]
    except (KeyError, FileNotFoundError):
        st.error(
            "Auth is not configured. Add a [auth] section with `username` and "
            "`password_hash` to .streamlit/secrets.toml (local) or your "
            "Streamlit Cloud app's Secrets settings."
        )
        st.stop()

    st.title("🔒 Portfolio Dashboard — Login")
    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")

        if submitted:
            if username == expected_username and _hash(password) == expected_password_hash:
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Incorrect username or password.")

    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python auth.py <your_chosen_password>")
        print("Prints the SHA-256 hash to paste into secrets.toml as password_hash.")
        sys.exit(1)
    print(_hash(sys.argv[1]))
