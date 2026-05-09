from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Wallet(Base):
    __tablename__ = "wallet"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "xpub" | "address"
    value: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    script_type: Mapped[str] = mapped_column(String(16), nullable=False)
    gap_limit: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)

    addresses: Mapped[list[Address]] = relationship(
        back_populates="wallet", cascade="all, delete-orphan"
    )


class Address(Base):
    __tablename__ = "address"
    __table_args__ = (UniqueConstraint("wallet_id", "address", name="uq_address_per_wallet"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_id: Mapped[int] = mapped_column(ForeignKey("wallet.id", ondelete="CASCADE"))
    address: Mapped[str] = mapped_column(String, nullable=False, index=True)
    derivation_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chain: Mapped[str] = mapped_column(String(8), nullable=False, default="receive")
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    wallet: Mapped[Wallet] = relationship(back_populates="addresses")


class Transaction(Base):
    __tablename__ = "transaction"

    txid: Mapped[str] = mapped_column(String(64), primary_key=True)
    block_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fee_sats: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    classification: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    btc_price_fiat: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_ccy: Mapped[str | None] = mapped_column(String(3), nullable=True)

    ios: Mapped[list[TxIO]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class TxIO(Base):
    __tablename__ = "tx_io"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txid: Mapped[str] = mapped_column(
        String(64), ForeignKey("transaction.txid", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(4), nullable=False)  # "in" | "out"
    address: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    amount_sats: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    wallet_id: Mapped[int | None] = mapped_column(
        ForeignKey("wallet.id", ondelete="SET NULL"), nullable=True
    )

    transaction: Mapped[Transaction] = relationship(back_populates="ios")


class Lot(Base):
    __tablename__ = "lot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txid: Mapped[str] = mapped_column(String(64), index=True)
    amount_sats: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_basis_fiat: Mapped[float] = mapped_column(Float, nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    remaining_sats: Mapped[int] = mapped_column(BigInteger, nullable=False)


class RealizedGain(Base):
    __tablename__ = "realized_gain"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    txid: Mapped[str] = mapped_column(String(64), index=True)
    lot_id: Mapped[int] = mapped_column(ForeignKey("lot.id", ondelete="CASCADE"))
    sats_sold: Mapped[int] = mapped_column(BigInteger, nullable=False)
    proceeds_fiat: Mapped[float] = mapped_column(Float, nullable=False)
    cost_basis_fiat: Mapped[float] = mapped_column(Float, nullable=False)
    gain_fiat: Mapped[float] = mapped_column(Float, nullable=False)
    sold_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class PriceCache(Base):
    __tablename__ = "price_cache"

    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    base_ccy: Mapped[str] = mapped_column(String(3), primary_key=True)
    btc_price: Mapped[float] = mapped_column(Float, nullable=False)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
