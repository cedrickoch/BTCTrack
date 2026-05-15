from __future__ import annotations

from datetime import datetime

import streamlit as st

from btctrack import backup
from btctrack.sync import get_last_sync, run_sync
from btctrack.ui.privacy import is_feature_enabled, render_sidebar_lock, set_password

render_sidebar_lock()
st.title("Settings")

# Flash a one-shot success banner that was set right before an st.rerun().
_FLASH_KEY = "_btctrack_flash_success"
if _flash := st.session_state.pop(_FLASH_KEY, None):
    st.success(_flash)

st.subheader("Privacy mode")

if is_feature_enabled():
    st.success("🔒 Privacy mode is active.")
    st.caption(
        "A password is set. Fiat and BTC values are masked until you unlock them "
        "from the sidebar. The password cannot be changed, shown, or removed here — "
        "it is stored in the database and travels with backups."
    )
else:
    st.caption(
        "Set a password to mask fiat and BTC values until unlocked. Optional — leave "
        "unset to keep all values visible. Once set, the password cannot be changed "
        "or removed from the UI."
    )
    with st.form("_btctrack_set_privacy_pw"):
        _pw1 = st.text_input("New password", type="password")
        _pw2 = st.text_input("Confirm password", type="password")
        _pw_submitted = st.form_submit_button("Set password", type="primary")
    if _pw_submitted:
        if not _pw1:
            st.error("Password cannot be empty.")
        elif _pw1 != _pw2:
            st.error("Passwords do not match.")
        elif len(_pw1) < 8:
            st.error("Password must be at least 8 characters.")
        else:
            try:
                set_password(_pw1)
            except ValueError as e:
                st.error(str(e))
            else:
                st.session_state[_FLASH_KEY] = "Privacy mode enabled."
                st.rerun()

st.divider()
st.subheader("Sync")

last = get_last_sync()
st.write(f"Last sync: **{last.strftime('%Y-%m-%d %H:%M:%S %Z') if last else 'never'}**")

if st.button("Run sync now", type="primary"):
    with st.spinner("Syncing — derive xpubs, fetch histories, ingest txs, look up prices…"):
        try:
            res = run_sync()
            st.success(
                f"Sync complete: {res.wallets} wallets · {res.addresses} new addresses · "
                f"{res.new_txs} new tx · {res.classified} (re)classified · "
                f"{res.prices_filled} prices filled from snapshot"
            )
        except Exception as e:
            st.error(f"Sync failed: {e}")
            st.exception(e)

st.divider()
st.subheader("Export data")
st.caption(
    "Take a snapshot of the current database — wallets, addresses, transactions, "
    "lots, realised gains, settings — for migrating to another host or for offline "
    "backup."
)

_EXPORT_KEY = "_btctrack_export_bytes"
_EXPORT_NAME_KEY = "_btctrack_export_filename"

encrypt = st.checkbox(
    "Encrypt with passphrase",
    value=True,
    help=(
        "Encrypted backups protect xpubs in transit (USB stick, scp, cloud). "
        "Uncheck only if you're moving the file over a fully trusted channel "
        "and want a raw SQLite file you can open with sqlite3."
    ),
)
exp_pw1 = exp_pw2 = ""
if encrypt:
    c1, c2 = st.columns(2)
    exp_pw1 = c1.text_input("Passphrase", type="password", key="_btctrack_exp_pw1")
    exp_pw2 = c2.text_input("Confirm passphrase", type="password", key="_btctrack_exp_pw2")

if st.button("Prepare export"):
    st.session_state.pop(_EXPORT_KEY, None)
    st.session_state.pop(_EXPORT_NAME_KEY, None)
    if encrypt and not exp_pw1:
        st.error("Passphrase is required when encrypting.")
    elif encrypt and exp_pw1 != exp_pw2:
        st.error("Passphrases do not match.")
    else:
        try:
            data = backup.export_bytes(exp_pw1 if encrypt else None)
        except Exception as e:  # pragma: no cover — UI error path
            st.error(f"Export failed: {e}")
        else:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            suffix = "btctrk.enc" if encrypt else "btctrk"
            st.session_state[_EXPORT_KEY] = data
            st.session_state[_EXPORT_NAME_KEY] = f"btctrack-{ts}.{suffix}"
            st.success(f"Ready: {len(data):,} bytes. Click the download button below.")

if _EXPORT_KEY in st.session_state:
    st.download_button(
        "Download backup",
        data=st.session_state[_EXPORT_KEY],
        file_name=st.session_state[_EXPORT_NAME_KEY],
        mime="application/octet-stream",
        type="primary",
    )

st.divider()
st.subheader("Import data")
st.warning(
    "**Import replaces ALL current data.** Your current database is moved to "
    "`btctrack.db.bak` first so you can recover it manually if needed."
)

uploaded = st.file_uploader(
    "Backup file",
    type=["btctrk", "enc"],
    accept_multiple_files=False,
    help="A `.btctrk` (raw) or `.btctrk.enc` (encrypted) file produced by Export.",
)
imp_pw = ""
needs_pw = False
if uploaded is not None:
    raw_bytes = uploaded.getvalue()
    needs_pw = backup.is_encrypted(raw_bytes)
    if needs_pw:
        imp_pw = st.text_input(
            "Passphrase", type="password", key="_btctrack_imp_pw"
        )
    else:
        st.info("File is not encrypted — no passphrase needed.")

if st.button("Import", type="primary", disabled=uploaded is None):
    if needs_pw and not imp_pw:
        st.error("This backup is encrypted; enter the passphrase.")
    else:
        try:
            res = backup.import_bytes(
                uploaded.getvalue(),  # type: ignore[union-attr]
                imp_pw if needs_pw else None,
            )
        except backup.BackupError as e:
            st.error(f"Import failed: {e}")
        except Exception as e:  # pragma: no cover — defensive
            st.error(f"Import failed: {e}")
        else:
            st.session_state[_FLASH_KEY] = (
                f"Import successful — {res.wallets} wallets · "
                f"{res.addresses} addresses · {res.transactions} transactions "
                "restored. Previous database moved to btctrack.db.bak."
            )
            st.rerun()
