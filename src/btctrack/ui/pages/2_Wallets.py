from __future__ import annotations

import pandas as pd
import streamlit as st
from sqlalchemy import select

from btctrack.chain.derive import derive_chain
from btctrack.chain.multisig import derive_multisig_address, parse_descriptor
from btctrack.config import get_settings
from btctrack.db.models import Address, TxIO, Wallet
from btctrack.db.session import session_scope
from btctrack.sync import add_wallet, remove_wallet, rename_wallet
from btctrack.ui.privacy import mask_dataframe, render_sidebar_lock

render_sidebar_lock()
st.title("Wallets")

settings = get_settings()

SCRIPT_TYPE_LABELS = {
    "p2wpkh": "Native SegWit / BIP84 (bc1q…) — Sparrow default",
    "p2sh-p2wpkh": "Wrapped SegWit / BIP49 (3…)",
    "p2pkh": "Legacy / BIP44 (1…)",
    "p2tr": "Taproot / BIP86 (bc1p…)",
}

MULTISIG_SCRIPT_LABELS = {
    "p2wsh": "Native SegWit multisig (bc1q…)",
    "p2sh-p2wsh": "Wrapped SegWit multisig (3…)",
    "p2sh": "Legacy P2SH multisig (3…)",
}

tab_single, tab_multisig = st.tabs(["Single key (xpub / address)", "Multisig"])

# -------------------- single-key tab --------------------
with tab_single:
    kind = st.radio(
        "Kind",
        ["xpub", "address"],
        index=0,
        horizontal=True,
        key="single_kind",
        help=(
            "Pick **xpub** to import a whole account by extended public key. "
            "Pick **address** when you only have a single receive address "
            "(e.g. an old wallet whose xpub you no longer have)."
        ),
    )

    if kind == "xpub":
        st.caption(
            "**Important for Sparrow users:** the script type below must match "
            "Sparrow's *Script Type* (Settings → Script Type), **not** the prefix "
            "of your extended key. Sparrow lets you toggle between `xpub`/`ypub`/"
            "`zpub` formats independently of the script type, so the prefix is "
            "unreliable. Pick the type your Sparrow wallet actually uses."
        )
        with st.form("add_xpub_form", clear_on_submit=False):
            label = st.text_input("Label", placeholder="Cold storage", key="xpub_label")
            value = st.text_input(
                "Extended public key",
                placeholder="xpub… / ypub… / zpub…",
                key="xpub_value",
            )
            script_type = st.selectbox(
                "Script type",
                list(SCRIPT_TYPE_LABELS.keys()),
                index=0,
                format_func=lambda k: SCRIPT_TYPE_LABELS[k],
                help=(
                    "Must match Sparrow's script type. Default is Native SegWit "
                    "(BIP84), Sparrow's default for new wallets."
                ),
                key="xpub_script_type",
            )
            gap_limit = st.number_input(
                "Gap limit",
                min_value=1,
                max_value=200,
                value=settings.gap_limit,
                step=1,
                key="xpub_gap",
            )
            c1, c2 = st.columns(2)
            preview_clicked = c1.form_submit_button("Preview first 3 addresses")
            submitted = c2.form_submit_button("Add wallet", type="primary")

        if preview_clicked and value.strip():
            try:
                preview = derive_chain(
                    value.strip(), script_type, "receive", count=3  # type: ignore[arg-type]
                )
                st.info(
                    "**Verify these match the first 3 receive addresses in Sparrow** "
                    "(Sparrow → Addresses tab). If they don't, change the script type."
                )
                st.code(
                    "\n".join(f"{i}: {a.address}" for i, a in enumerate(preview)),
                    language="text",
                )
            except Exception as e:
                st.error(f"Could not derive preview: {e}")

        if submitted:
            if not label.strip() or not value.strip():
                st.error("Label and value are required.")
            else:
                try:
                    wid = add_wallet(
                        label=label.strip(),
                        kind="xpub",
                        value=value.strip(),
                        script_type=script_type,
                        gap_limit=int(gap_limit),
                    )
                    st.success(
                        f"Added wallet #{wid} as {SCRIPT_TYPE_LABELS[script_type]}. "
                        "Run a sync from Settings."
                    )
                except Exception as e:
                    st.error(f"Could not add wallet: {e}")
    else:
        st.caption(
            "Import a single mainnet BTC address. Use this when you only have "
            "the address itself — for example, an older receive address whose "
            "xpub you no longer have. Only this one address is tracked; the "
            "wallet's other addresses won't be discovered."
        )
        with st.form("add_address_form", clear_on_submit=False):
            addr_label = st.text_input(
                "Label", placeholder="Old receive address", key="addr_label"
            )
            addr_value = st.text_input(
                "BTC address",
                placeholder="bc1q… / bc1p… / 3… / 1…",
                key="addr_value",
            )
            addr_submitted = st.form_submit_button("Add address", type="primary")

        if addr_submitted:
            if not addr_label.strip() or not addr_value.strip():
                st.error("Label and address are required.")
            else:
                try:
                    wid = add_wallet(
                        label=addr_label.strip(),
                        kind="address",
                        value=addr_value.strip(),
                    )
                    st.success(
                        f"Added address wallet #{wid}. Run a sync from Settings."
                    )
                except ValueError as e:
                    st.error(f"Invalid address: {e}")
                except Exception as e:
                    st.error(f"Could not add wallet: {e}")

# -------------------- multisig tab --------------------
with tab_multisig:
    st.caption(
        "Paste an output descriptor exported from Sparrow "
        "(File → Export → Output Descriptor). Supported wrappers: "
        "`wsh(...)`, `sh(wsh(...))`, `sh(...)`. Inner expression must be "
        "`multi(...)` or `sortedmulti(...)`. The script type and threshold "
        "are read from the descriptor — no separate selection needed."
    )
    with st.form("add_multisig_form", clear_on_submit=False):
        ms_label = st.text_input(
            "Label", placeholder="2-of-3 cold storage", key="ms_label"
        )
        descriptor = st.text_area(
            "Output descriptor",
            placeholder=(
                "wsh(sortedmulti(2,"
                "[fingerprint/84h/0h/0h]zpub.../0/*,"
                "[fingerprint/84h/0h/0h]zpub.../0/*,"
                "[fingerprint/84h/0h/0h]zpub.../0/*"
                "))#checksum"
            ),
            height=160,
            key="ms_descriptor",
        )
        ms_gap = st.number_input(
            "Gap limit",
            min_value=1,
            max_value=200,
            value=settings.gap_limit,
            step=1,
            key="ms_gap",
        )
        c1, c2 = st.columns(2)
        ms_preview_clicked = c1.form_submit_button("Preview first 3 addresses")
        ms_submitted = c2.form_submit_button("Add multisig wallet", type="primary")

    if ms_preview_clicked and descriptor.strip():
        try:
            ms = parse_descriptor(descriptor.strip())
            st.success(
                f"Parsed: {ms.threshold}-of-{ms.n} "
                f"{MULTISIG_SCRIPT_LABELS.get(ms.script_type, ms.script_type)} "
                f"({'sortedmulti' if ms.sorted_keys else 'multi'})"
            )
            preview_lines = [
                f"{i}: {derive_multisig_address(ms, 'receive', i)}" for i in range(3)
            ]
            st.info(
                "**Verify these match the first 3 receive addresses in Sparrow** "
                "(Sparrow → Addresses tab). If they don't, double-check the descriptor."
            )
            st.code("\n".join(preview_lines), language="text")
        except Exception as e:
            st.error(f"Could not parse / derive: {e}")

    if ms_submitted:
        if not ms_label.strip() or not descriptor.strip():
            st.error("Label and descriptor are required.")
        else:
            try:
                wid = add_wallet(
                    label=ms_label.strip(),
                    kind="multisig",
                    value=descriptor.strip(),
                    gap_limit=int(ms_gap),
                )
                st.success(
                    f"Added multisig wallet #{wid}. Run a sync from Settings."
                )
            except Exception as e:
                st.error(f"Could not add wallet: {e}")

st.subheader("Configured wallets")

with session_scope() as s:
    wallets = s.execute(select(Wallet)).scalars().all()
    rows = []
    for w in wallets:
        addr_count = s.execute(
            select(Address).where(Address.wallet_id == w.id)
        ).scalars().all()
        ios = s.execute(
            select(TxIO).where(TxIO.wallet_id == w.id, TxIO.owned.is_(True))
        ).scalars().all()
        sats = sum(io.amount_sats if io.direction == "out" else -io.amount_sats for io in ios)
        rows.append(
            {
                "id": w.id,
                "label": w.label,
                "kind": w.kind,
                "script_type": w.script_type,
                "gap_limit": w.gap_limit,
                "addresses": len(addr_count),
                "balance_sats": sats,
                "balance_btc": sats / 100_000_000,
                "value": w.value if len(w.value) <= 24 else w.value[:10] + "…" + w.value[-6:],
            }
        )

if not rows:
    st.info("No wallets yet — add one above.")
else:
    st.dataframe(
        mask_dataframe(
            pd.DataFrame(rows),
            btc_cols=("balance_btc",),
            sats_cols=("balance_sats",),
        ),
        width="stretch",
        hide_index=True,
    )
    options = {f"#{r['id']} — {r['label']}": r["id"] for r in rows}
    labels_by_id = {r["id"]: r["label"] for r in rows}

    col_rename, col_remove = st.columns(2)

    with col_rename:
        st.markdown("**Rename a wallet**")
        rename_pick = st.selectbox(
            "Wallet to rename", ["—"] + list(options.keys()), key="rename_pick"
        )
        if rename_pick != "—":
            wid = options[rename_pick]
            with st.form("rename_wallet_form", clear_on_submit=False):
                new_label = st.text_input(
                    "New label", value=labels_by_id[wid], key="rename_new_label"
                )
                rename_submitted = st.form_submit_button("Save label", type="primary")
            if rename_submitted:
                try:
                    rename_wallet(wid, new_label)
                    st.success("Renamed. Reload page.")
                except ValueError as e:
                    st.error(str(e))

    with col_remove:
        st.markdown("**Remove a wallet**")
        pick = st.selectbox(
            "Wallet to remove", ["—"] + list(options.keys()), key="remove_pick"
        )
        if pick != "—":
            if st.button("Remove (cascades addresses)", type="primary"):
                remove_wallet(options[pick])
                st.success("Removed. Reload page.")
