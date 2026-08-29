"""
BLUEBLOOD FLOOR v11
Sala 2D + 10 agenti + prezzi live Yahoo (yfinance + fallback HTTP)
Paper trading. Non esegue ordini reali.
Capitale iniziale modificabile + reset test
Uso personale. Non esegue ordini reali.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

import requests

try:
    import pandas as pd
except Exception:
    pd = None
try:
    import yfinance as yf
except Exception:
    yf = None
from kivy.clock import Clock, mainthread
from kivy.core.window import Window
from kivy.utils import platform
from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFlatButton, MDRaisedButton
from kivymd.uix.card import MDCard
from kivymd.uix.dialog import MDDialog
from kivymd.uix.gridlayout import MDGridLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.screen import MDScreen
try:
    from kivymd.uix.screenmanager import MDScreenManager
except Exception:
    from kivy.uix.screenmanager import ScreenManager as MDScreenManager
from kivymd.uix.textfield import MDTextField
try:
    from kivymd.uix.toolbar import MDTopAppBar
except Exception:
    from kivymd.uix.toolbar import MDToolbar as MDTopAppBar
from kivy.graphics import Color, Line
from kivy.uix.image import Image
from kivy.uix.widget import Widget

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_FILE = os.path.join(BASE_DIR, "icon.png")
JOURNAL_FILE = os.path.join(BASE_DIR, "journal.csv")
STATE_FILE = os.path.join(BASE_DIR, "positions.json")

if os.path.exists(ICON_FILE):
    try:
        Window.set_icon(ICON_FILE)
    except Exception:
        pass

if platform != "android":
    Window.size = (1440, 900)
    Window.minimum_width = 1100
    Window.minimum_height = 700
Window.clearcolor = (0.02, 0.03, 0.06, 1)

CONFIG = {
    "capital": 100.00,
    "max_risk_per_trade": 0.009,
    "risk_per_trade": 0.007,
    "max_position_pct": 0.10,
    "max_open_positions": 3,
    "max_trades_per_day": 5,
    "min_reward_risk": 1.9,
    "atr_stop_mult": 1.35,
    "scan_every_seconds": 0.55,
    "cache_ttl_seconds": 10,
    "timeframe": "1m",
    "chart_tf": "5m",
    "htf_timeframe": "15m",
    "lookback_period": "5d",
    "min_agent_align": 5,
    "min_strategy_conf": 0.72,
    "max_volatility_pct": 2.00,
    "symbol_cooldown_seconds": 180,
    "symbols": [
        "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ",
        "BTC-USD", "ETH-USD", "EURUSD=X", "GC=F",
    ],
}

DESKS = [
    ("DATA", "Leo", "Dati", "L", (0.07, 0.16, 0.30, 1)),
    ("TECH", "Mara", "Tecnica", "M", (0.06, 0.20, 0.26, 1)),
    ("PACT", "Nico", "Price Action", "N", (0.10, 0.14, 0.26, 1)),
    ("MOM", "Sara", "Momentum", "S", (0.08, 0.16, 0.22, 1)),
    ("VOL", "Vale", "Volume", "V", (0.09, 0.15, 0.24, 1)),
    ("REG", "Omar", "Regime", "O", (0.07, 0.13, 0.20, 1)),
    ("LEV", "Lara", "Livelli", "A", (0.09, 0.12, 0.22, 1)),
    ("STR", "Gio", "Strategia", "G", (0.05, 0.18, 0.16, 1)),
    ("RISK", "Rita", "Rischio", "R", (0.22, 0.08, 0.10, 1)),
    ("CONS", "Ciro", "Consenso", "C", (0.14, 0.10, 0.24, 1)),
]


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class Msg:
    name: str
    signal: Side
    conf: float
    reason: str
    extra: Dict = field(default_factory=dict)


@dataclass
class Idea:
    symbol: str
    direction: Side
    entry: float
    stop: float
    tp: float
    size_pct: float
    conf: float
    reason: str


@dataclass
class Position:
    symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    size_pct: float
    open_time: str
    highest: float = 0.0
    lowest: float = 999999.0
    status: str = "OPEN"


def _closes(df) -> List[float]:
    if hasattr(df, "rows"):
        return [float(r["close"]) for r in df.rows]
    return [float(x) for x in df["close"].tolist()]


def _rsi(close, n: int = 14) -> float:
    vals = list(close) if not hasattr(close, "tolist") else list(close.tolist())
    if hasattr(close, "rows"):
        vals = _closes(close)
    if len(vals) < n + 2:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(vals)):
        d = vals[i] - vals[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    g = sum(gains[-n:]) / n
    l = sum(losses[-n:]) / n
    if l <= 1e-12:
        return 80.0
    rs = g / l
    return float(100 - 100 / (1 + rs))


def _atr(df, n: int = 14) -> float:
    rows = df.rows if hasattr(df, "rows") else None
    if rows is None:
        last = float(df["close"].iloc[-1])
        try:
            hl = df["high"] - df["low"]
            hc = (df["high"] - df["close"].shift()).abs()
            lc = (df["low"] - df["close"].shift()).abs()
            tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
            v = tr.rolling(n).mean().iloc[-1]
            return float(v) if v == v else last * 0.008
        except Exception:
            return last * 0.008
    if len(rows) < n + 2:
        return float(rows[-1]["close"]) * 0.008
    trs = []
    for i in range(1, len(rows)):
        h, l, c_prev = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return sum(trs[-n:]) / n


NEWS_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def _ok(x) -> bool:
    try:
        return x is not None and x == x
    except Exception:
        return False


class _Iloc:
    def __init__(self, vals):
        self.vals = vals

    def __getitem__(self, i):
        return self.vals[i]


class Col:
    def __init__(self, vals):
        self.vals = [float(v) if v is not None else float("nan") for v in vals]

    def __getitem__(self, i):
        return self.vals[i]

    def __iter__(self):
        return iter(self.vals)

    @property
    def iloc(self):
        return _Iloc(self.vals)

    def rolling(self, n):
        class _R:
            def __init__(self, vals, nn):
                self.vals, self.n = vals, nn

            def mean(self):
                out = [float("nan")] * min(max(self.n - 1, 0), len(self.vals))
                for i in range(self.n - 1, len(self.vals)):
                    chunk = self.vals[i - self.n + 1 : i + 1]
                    out.append(sum(chunk) / len(chunk))
                return Col(out)

        return _R(self.vals, n)

    def mean(self):
        xs = [x for x in self.vals if _ok(x)]
        return sum(xs) / len(xs) if xs else float("nan")

    def pct_change(self, periods: int = 1):
        out = [float("nan")] * min(periods, len(self.vals))
        for i in range(periods, len(self.vals)):
            p = self.vals[i - periods]
            out.append((self.vals[i] / p - 1.0) if p else float("nan"))
        return Col(out)

    def std(self):
        xs = [x for x in self.vals if _ok(x)]
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    def max(self):
        xs = [x for x in self.vals if _ok(x)]
        return max(xs) if xs else 0.0

    def min(self):
        xs = [x for x in self.vals if _ok(x)]
        return min(xs) if xs else 0.0

    def tail(self, n):
        return Col(self.vals[-n:])


class MiniDF:
    def __init__(self, rows: List[dict]):
        self.rows = rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, key):
        if isinstance(key, str):
            return Col([r.get(key, 0) for r in self.rows])
        return self.rows[key]

    @property
    def iloc(self):
        return _Iloc(self.rows)

    def tail(self, n):
        return MiniDF(self.rows[-n:])


def sparkline(values) -> str:
    blocks = "▁▂▃▄▅▆▇█"
    vals = [float(v) for v in values if _ok(v)]
    if len(vals) < 2:
        return "grafico n/d"
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    bar = "".join(blocks[min(7, int((v - lo) / span * 7))] for v in vals[-36:])
    chg = (vals[-1] / vals[0] - 1) * 100
    return f"{bar}  {vals[-1]:.4f}  ({chg:+.2f}%)  L{lo:.2f}-H{hi:.2f}"


def fetch_news(symbol: str) -> List[str]:
    now = time.time()
    hit = NEWS_CACHE.get(symbol)
    if hit and now - hit[0] < 180:
        return hit[1]
    titles: List[str] = []
    if yf is not None:
        try:
            items = yf.Ticker(symbol).news or []
            for it in items[:4]:
                title = None
                if isinstance(it, dict):
                    title = it.get("title")
                    if not title and isinstance(it.get("content"), dict):
                        title = it["content"].get("title")
                if title:
                    titles.append(str(title)[:110])
        except Exception:
            titles = hit[1] if hit else []
    if not titles:
        titles = [f"{symbol} live feed attivo", "Paper trading: copia il biglietto sul broker"]
    NEWS_CACHE[symbol] = (now, titles)
    return titles


def journal_tail(n: int = 8) -> str:
    if not os.path.exists(JOURNAL_FILE):
        return "Nessuno storico ancora."
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            rows = [ln.strip() for ln in f if ln.strip()]
        if len(rows) <= 1:
            return "Storico vuoto."
        body = rows[1:]
        pick = [r for r in body if any(k in r for k in ("OPEN", "CLOSE", "VETO", "RESET"))] or body
        lines = []
        for r in pick[-n:]:
            p = r.split(",", 3)
            if len(p) >= 4:
                lines.append(f"{p[0][-8:]}  {p[1]}  {p[2]}  {p[3]}")
            else:
                lines.append(r[-90:])
        return "\n".join(lines)
    except Exception:
        return "Storico non leggibile."


def _yahoo_chart(symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    """Prezzi live via HTTP. Serve sia al PC sia, dopo, all'APK con requests."""
    range_map = {"1d": "1d", "5d": "5d", "10d": "1mo", "1mo": "1mo"}
    rng = range_map.get(period, "5d")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = requests.get(
            url,
            params={"interval": interval, "range": rng, "includePrePost": "false"},
            headers={"User-Agent": "Mozilla/5.0 BlueBlood/11"},
            timeout=8,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        rows = []
        vol_s = q.get("volume") or [0] * len(ts)
        for i, t in enumerate(ts):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            rows.append({
                "timestamp": datetime.fromtimestamp(t),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(vol_s[i] or 0),
            })
        if len(rows) < 32:
            return None
        return MiniDF(rows)
    except Exception:
        return None


def _norm_history(df):
    if pd is None or df is None or getattr(df, "empty", True):
        return None
    out = df.reset_index()
    out.columns = [str(c).lower() for c in out.columns]
    if "datetime" in out.columns:
        out.rename(columns={"datetime": "timestamp"}, inplace=True)
    elif "date" in out.columns:
        out.rename(columns={"date": "timestamp"}, inplace=True)
    need = ["open", "high", "low", "close"]
    if not all(c in out.columns for c in need):
        return None
    if "volume" not in out.columns:
        out["volume"] = 0.0
    return out[["timestamp", "open", "high", "low", "close", "volume"]].copy()


class MarketData:
    def __init__(self):
        self.lock = threading.Lock()
        self.cache: Dict[Tuple[str, str], Tuple[float, object]] = {}

    def get(self, symbol: str, interval: str, period: str) -> Optional[pd.DataFrame]:
        key = (symbol, interval)
        now = time.time()
        with self.lock:
            hit = self.cache.get(key)
            if hit and now - hit[0] < CONFIG["cache_ttl_seconds"]:
                return hit[1]
        df = _yahoo_chart(symbol, interval, period)
        if (df is None or len(df) < 32) and yf is not None and pd is not None:
            try:
                raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
                norm = _norm_history(raw)
                if norm is not None and len(norm) >= 32:
                    recs = norm.to_dict("records")
                    df = MiniDF([{
                        "timestamp": r.get("timestamp"),
                        "open": float(r["open"]),
                        "high": float(r["high"]),
                        "low": float(r["low"]),
                        "close": float(r["close"]),
                        "volume": float(r.get("volume") or 0),
                    } for r in recs])
            except Exception:
                pass
        if df is None or len(df) < 32:
            return hit[1] if hit else None
        with self.lock:
            self.cache[key] = (now, df)
        return df


class Agents:
    @staticmethod
    def data(df: pd.DataFrame) -> Msg:
        last, prev = df.iloc[-1], df.iloc[-2]
        chg = (float(last["close"]) / float(prev["close"]) - 1) * 100
        vol = float(df["close"].pct_change().std() * 100)
        return Msg("DATA", Side.NEUTRAL, 0.95, f"{float(last['close']):.4f} ({chg:+.2f}%)")

    @staticmethod
    def technical(df: pd.DataFrame) -> Msg:
        c = df["close"]
        s8, s21 = c.rolling(8).mean().iloc[-1], c.rolling(21).mean().iloc[-1]
        r = _rsi(c)
        sig, conf, why = Side.NEUTRAL, 0.46, "neutro"
        if _ok(s8) and _ok(s21):
            if s8 > s21 and 34 < r < 66:
                sig, conf, why = Side.LONG, 0.73, f"SMA+ RSI {r:.0f}"
            elif s8 < s21 and 34 < r < 66:
                sig, conf, why = Side.SHORT, 0.73, f"SMA- RSI {r:.0f}"
        if r >= 72 or r <= 28:
            conf *= 0.62
            why += " estremo"
        return Msg("TECH", sig, min(conf, 0.88), why)

    @staticmethod
    def price_action(df: pd.DataFrame) -> Msg:
        a, b = df.iloc[-1], df.iloc[-2]
        if a["close"] > a["open"] and b["close"] < b["open"] and a["close"] > b["open"] and a["open"] < b["close"]:
            return Msg("PACT", Side.LONG, 0.77, "engulfing +")
        if a["close"] < a["open"] and b["close"] > b["open"] and a["close"] < b["open"] and a["open"] > b["close"]:
            return Msg("PACT", Side.SHORT, 0.77, "engulfing -")
        return Msg("PACT", Side.NEUTRAL, 0.48, "no pattern")

    @staticmethod
    def momentum(df: pd.DataFrame) -> Msg:
        r3 = df["close"].pct_change(3).iloc[-1]
        r8 = df["close"].pct_change(8).iloc[-1]
        if not _ok(r3) or not _ok(r8):
            return Msg("MOM", Side.NEUTRAL, 0.45, "n/d")
        if r3 > 0.004 and r8 > 0:
            return Msg("MOM", Side.LONG, 0.66, f"+{r3*100:.2f}%")
        if r3 < -0.004 and r8 < 0:
            return Msg("MOM", Side.SHORT, 0.66, f"{r3*100:.2f}%")
        return Msg("MOM", Side.NEUTRAL, 0.50, "piatto")

    @staticmethod
    def volume(df: pd.DataFrame) -> Msg:
        v = df["volume"]
        mean_v = float(v.rolling(20).mean().iloc[-1] or 0)
        if float(v.iloc[-1]) <= 0 or mean_v <= 0:
            return Msg("VOL", Side.NEUTRAL, 0.40, "n/d")
        ratio = float(v.iloc[-1] / (mean_v + 1e-9))
        chg = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1)
        if ratio >= 1.35 and chg > 0:
            return Msg("VOL", Side.LONG, 0.64, f"x{ratio:.2f}")
        if ratio >= 1.35 and chg < 0:
            return Msg("VOL", Side.SHORT, 0.64, f"x{ratio:.2f}")
        return Msg("VOL", Side.NEUTRAL, 0.52, f"x{ratio:.2f}")

    @staticmethod
    def regime(df: pd.DataFrame) -> Msg:
        vol = float(df["close"].pct_change().std() * 100)
        trend = 0.0
        if len(df) >= 20:
            trend = (float(df["close"].iloc[-1]) / float(df["close"].iloc[-20]) - 1) * 100
        if vol > CONFIG["max_volatility_pct"]:
            return Msg("REG", Side.NEUTRAL, 0.86, f"vol {vol:.2f}%")
        if trend > 2.0:
            return Msg("REG", Side.LONG, 0.76, f"up {trend:.2f}%")
        if trend < -2.0:
            return Msg("REG", Side.SHORT, 0.76, f"dn {trend:.2f}%")
        return Msg("REG", Side.NEUTRAL, 0.67, "laterale")

    @staticmethod
    def levels(df: pd.DataFrame) -> Msg:
        w = df.tail(30)
        px = float(df["close"].iloc[-1])
        hi, lo = float(w["high"].max()), float(w["low"].min())
        pos = (px - lo) / max(hi - lo, 1e-9)
        if pos < 0.18:
            return Msg("LEV", Side.LONG, 0.60, "minimo")
        if pos > 0.82:
            return Msg("LEV", Side.SHORT, 0.60, "massimo")
        return Msg("LEV", Side.NEUTRAL, 0.50, "mid")

    @staticmethod
    def htf(df15: Optional[pd.DataFrame]) -> Msg:
        if df15 is None or len(df15) < 25:
            return Msg("HTF", Side.NEUTRAL, 0.40, "no 15m")
        c = df15["close"]
        s8, s21 = c.rolling(8).mean().iloc[-1], c.rolling(21).mean().iloc[-1]
        if _ok(s8) and _ok(s21) and s8 > s21:
            return Msg("HTF", Side.LONG, 0.70, "15m +")
        if _ok(s8) and _ok(s21) and s8 < s21:
            return Msg("HTF", Side.SHORT, 0.70, "15m -")
        return Msg("HTF", Side.NEUTRAL, 0.50, "15m =")

    @staticmethod
    def strategy(msgs: List[Msg]) -> Msg:
        long_s = sum(m.conf for m in msgs if m.signal == Side.LONG)
        short_s = sum(m.conf for m in msgs if m.signal == Side.SHORT)
        n_long = sum(1 for m in msgs if m.signal == Side.LONG)
        n_short = sum(1 for m in msgs if m.signal == Side.SHORT)
        need = CONFIG["min_agent_align"]
        if n_long >= need and long_s > short_s + 0.80:
            return Msg("STR", Side.LONG, min(0.90, long_s / 4.5), f"L{n_long}")
        if n_short >= need and short_s > long_s + 0.80:
            return Msg("STR", Side.SHORT, min(0.90, short_s / 4.5), f"S{n_short}")
        return Msg("STR", Side.NEUTRAL, 0.47, f"L{n_long}/S{n_short}")

    @staticmethod
    def consensus(msgs: List[Msg]) -> Msg:
        score = {Side.LONG: 0.0, Side.SHORT: 0.0, Side.NEUTRAL: 0.0}
        for m in msgs:
            score[m.signal] += m.conf
        best = max(score, key=score.get)
        tot = sum(score.values()) or 1.0
        return Msg("CONS", best, score[best] / tot, f"L{score[Side.LONG]:.1f} S{score[Side.SHORT]:.1f}")

    @staticmethod
    def size(entry: float, stop: float) -> float:
        dist = abs(entry - stop) / entry if entry else 0
        if dist <= 0:
            return 0.0
        return float(min(CONFIG["risk_per_trade"] / dist, CONFIG["max_position_pct"]))

    @staticmethod
    def risk(idea: Idea, npos: int, ntrades: int, df: pd.DataFrame) -> Msg:
        risk_pct = abs(idea.entry - idea.stop) / idea.entry * idea.size_pct
        rr = abs(idea.tp - idea.entry) / max(abs(idea.entry - idea.stop), 1e-9)
        vol = float(df["close"].pct_change().std() * 100)
        veto = []
        if idea.conf < CONFIG["min_strategy_conf"]:
            veto.append("conf bassa")
        if risk_pct > CONFIG["max_risk_per_trade"]:
            veto.append("rischio alto")
        if ntrades >= CONFIG["max_trades_per_day"]:
            veto.append("limite day")
        if npos >= CONFIG["max_open_positions"]:
            veto.append("limite pos")
        if rr < CONFIG["min_reward_risk"]:
            veto.append("R:R")
        if vol > CONFIG["max_volatility_pct"]:
            veto.append("vol")
        if veto:
            return Msg("RISK", Side.NEUTRAL, 0.97, "VETO " + "/".join(veto), {"veto": True})
        return Msg("RISK", idea.direction, 0.91, f"OK R:R {rr:.2f}", {"veto": False})


class Engine:
    def __init__(self):
        self.capital = float(CONFIG["capital"])
        self.initial = float(CONFIG["capital"])
        self.positions: List[Position] = []
        self.trades_today = 0
        self.wins = 0
        self.losses = 0
        self.last_ticket = ""
        self.last_prices: Dict[str, float] = {}
        self.cooldown: Dict[str, float] = {}
        self.today = datetime.now().date().isoformat()
        self.data = MarketData()
        if not os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
                f.write("timestamp,symbol,action,details,capital\n")
        self.load()

    def log(self, symbol: str, action: str, details: str):
        line = "{},{},{},{},{}\n".format(
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol,
            action,
            details.replace(",", " "),
            round(self.capital, 2),
        )
        with open(JOURNAL_FILE, "a", encoding="utf-8") as f:
            f.write(line)

    def save(self):
        payload = {
            "capital": self.capital,
            "initial": self.initial,
            "trades_today": self.trades_today,
            "wins": self.wins,
            "losses": self.losses,
            "today": self.today,
            "positions": [asdict(p) for p in self.positions if p.status == "OPEN"],
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load(self):
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.capital = float(d.get("capital", self.capital))
            self.initial = float(d.get("initial", self.initial))
            if d.get("today") == self.today:
                self.trades_today = int(d.get("trades_today", 0))
            else:
                self.trades_today = 0
            self.wins = int(d.get("wins", 0))
            self.losses = int(d.get("losses", 0))
            for p in d.get("positions", []):
                pos = Position(**p)
                if pos.status == "OPEN":
                    self.positions.append(pos)
        except Exception:
            pass

    def reset(self, new_capital: float):
        self.capital = float(new_capital)
        self.initial = float(new_capital)
        self.positions = []
        self.trades_today = 0
        self.wins = 0
        self.losses = 0
        self.last_ticket = ""
        self.cooldown = {}
        self.today = datetime.now().date().isoformat()
        self.save()
        self.log("SYSTEM", "RESET", f"nuovo capitale {new_capital:.2f}")

    def pnl(self) -> float:
        return (self.capital / self.initial - 1) * 100 if self.initial else 0.0

    def update_positions(self, symbol: str, price: float) -> List[str]:
        ev, keep = [], []
        for p in self.positions:
            if p.symbol != symbol or p.status != "OPEN":
                keep.append(p)
                continue
            hit_sl = hit_tp = False
            pnl = 0.0
            if p.direction == "LONG":
                p.highest = max(p.highest, price)
                trail = p.highest - (p.highest - p.entry) * 0.40
                if price > p.entry and trail > p.stop_loss:
                    p.stop_loss = trail
                hit_sl = price <= p.stop_loss
                hit_tp = price >= p.take_profit
                if hit_sl or hit_tp:
                    pnl = ((p.stop_loss if hit_sl else p.take_profit) - p.entry) / p.entry
            else:
                p.lowest = min(p.lowest, price)
                trail = p.lowest + (p.entry - p.lowest) * 0.40
                if price < p.entry and trail < p.stop_loss:
                    p.stop_loss = trail
                hit_sl = price >= p.stop_loss
                hit_tp = price <= p.take_profit
                if hit_sl or hit_tp:
                    pnl = (p.entry - (p.stop_loss if hit_sl else p.take_profit)) / p.entry
            if hit_sl or hit_tp:
                reason = "STOP / TRAILING" if hit_sl else "TAKE PROFIT"
                self.capital = max(5.0, self.capital + self.capital * p.size_pct * pnl)
                p.status = "CLOSED"
                if pnl >= 0:
                    self.wins += 1
                else:
                    self.losses += 1
                self.log(p.symbol, f"CLOSE {reason}", f"{p.direction} {pnl*100:+.2f}%")
                ev.append(f"{p.symbol} {reason} | {pnl*100:+.2f}% | €{self.capital:.2f}")
            else:
                keep.append(p)
        self.positions = keep
        self.save()
        return ev

    def analyze(self, symbol: str) -> Dict:
        out = {"symbol": symbol, "ok": False, "price": None, "msgs": [], "idea": None,
               "events": [], "logs": [], "popup": None}
        df = self.data.get(symbol, CONFIG["timeframe"], CONFIG["lookback_period"])
        if df is None or len(df) < 32:
            out["logs"].append(f"{symbol} dati non disponibili")
            return out
        price = float(df["close"].iloc[-1])
        out["ok"] = True
        out["price"] = price
        self.last_prices[symbol] = price
        out["spark"] = f"{symbol}  {sparkline(df['close'].tail(36))}  {datetime.now().strftime('%H:%M:%S')}"
        cdf = self.data.get(symbol, CONFIG.get("chart_tf", "5m"), "5d") or df
        out["closes"] = list(cdf["close"].tail(60))
        out["chart_name"] = f"{symbol}  {CONFIG.get('chart_tf','5m')}"
        out["news"] = fetch_news(symbol)
        out["history"] = journal_tail(6)
        out["events"] = self.update_positions(symbol, price)
        df15 = self.data.get(symbol, CONFIG["htf_timeframe"], "10d")
        msgs = [
            Agents.data(df), Agents.technical(df), Agents.price_action(df),
            Agents.momentum(df), Agents.volume(df), Agents.regime(df),
            Agents.levels(df), Agents.htf(df15),
        ]
        strat = Agents.strategy(msgs)
        cons = Agents.consensus(msgs + [strat])
        msgs += [strat, cons]
        out["msgs"] = msgs
        if any(p.symbol == symbol and p.status == "OPEN" for p in self.positions):
            out["logs"].append(f"{symbol} già in posizione")
            return out
        last_ts = self.cooldown.get(symbol, 0)
        if time.time() - last_ts < CONFIG["symbol_cooldown_seconds"]:
            return out
        htf = next((m for m in msgs if m.name == "HTF"), None)
        if htf and htf.signal not in (Side.NEUTRAL, strat.signal) and strat.signal != Side.NEUTRAL:
            out["logs"].append(f"{symbol} bloccato dal 15m")
            return out
        if strat.signal == Side.NEUTRAL:
            return out
        if cons.signal != strat.signal:
            out["logs"].append(f"{symbol} consensus non allineato")
            return out
        dist = CONFIG["atr_stop_mult"] * _atr(df)
        rr = CONFIG["min_reward_risk"]
        if strat.signal == Side.LONG:
            entry, stop, tp = price, price - dist, price + dist * rr
        else:
            entry, stop, tp = price, price + dist, price - dist * rr
        idea = Idea(symbol, strat.signal, entry, stop, tp, Agents.size(entry, stop), strat.conf, strat.reason)
        risk = Agents.risk(idea, len(self.positions), self.trades_today, df)
        msgs.append(risk)
        out["msgs"] = msgs
        if risk.extra.get("veto"):
            out["logs"].append(f"{symbol} {risk.reason}")
            self.log(symbol, "VETO", risk.reason)
            out["popup"] = ("VETO", f"{symbol}\n{risk.reason}")
            return out
        self.positions.append(Position(
            symbol, idea.direction.value, idea.entry, idea.stop, idea.tp, idea.size_pct,
            datetime.now().strftime("%H:%M:%S"),
            highest=idea.entry if idea.direction == Side.LONG else 0.0,
            lowest=idea.entry if idea.direction == Side.SHORT else 999999.0,
        ))
        self.trades_today += 1
        self.cooldown[symbol] = time.time()
        self.save()
        self.log(symbol, f"OPEN {idea.direction.value}", f"E {idea.entry:.4f} SL {idea.stop:.4f} TP {idea.tp:.4f}")
        out["idea"] = idea
        self.last_ticket = (
            f"{symbol} {idea.direction.value} | Entry {idea.entry:.4f} | "
            f"SL {idea.stop:.4f} | TP {idea.tp:.4f} | Size {idea.size_pct*100:.1f}%"
        )
        out["logs"].append(f"{symbol} {idea.direction.value} APERTO")
        out["popup"] = (
            f"SEGNALE {idea.direction.value}",
            f"{symbol}\nEntry {idea.entry:.4f}\nSL {idea.stop:.4f}\nTP {idea.tp:.4f}\nSize {idea.size_pct*100:.1f}%",
        )
        return out


class PriceChart(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.points: List[float] = []
        self.bind(size=self.redraw, pos=self.redraw)

    def set_data(self, points):
        self.points = [float(x) for x in points if _ok(x)]
        self.redraw()

    def redraw(self, *_):
        self.canvas.after.clear()
        if len(self.points) < 2 or self.width < 20:
            return
        lo, hi = min(self.points), max(self.points)
        span = hi - lo or 1.0
        n = len(self.points)
        coords = []
        pad = 8
        for i, v in enumerate(self.points):
            x = self.x + pad + i * (self.width - 2 * pad) / (n - 1)
            y = self.y + pad + (v - lo) / span * (self.height - 2 * pad)
            coords.extend([x, y])
        with self.canvas.after:
            last = self.points[-1]
            first = self.points[0]
            if last >= first:
                Color(0.30, 0.90, 0.55, 1)
            else:
                Color(1.00, 0.40, 0.40, 1)
            Line(points=coords, width=1.4)


class DeskCard(MDCard):
    def __init__(self, code: str, name: str, role: str, avatar: str, color, **kwargs):
        super().__init__(orientation="vertical", padding=8, spacing=1,
                         md_bg_color=color, radius=[6], **kwargs)
        self.code = code
        self.avatar_base = avatar
        self.title = MDLabel(text=f"{name}  •  {role}", font_style="Caption",
                             theme_text_color="Custom", text_color=(0.92, 0.95, 1, 1),
                             size_hint_y=None, height=16)
        img_path = os.path.join(BASE_DIR, "desks", f"{code}.png")
        if os.path.exists(img_path):
            self.face = Image(source=img_path, size_hint_y=None, height=48, allow_stretch=True, keep_ratio=True)
        else:
            self.face = MDLabel(text=f"[{avatar}]", halign="center", font_style="H6",
                                theme_text_color="Custom", text_color=(0.93, 0.82, 0.42, 1),
                                size_hint_y=None, height=28)
        self.mood = MDLabel(text="STANDBY", halign="center", font_style="Caption",
                            theme_text_color="Custom", text_color=(0.70, 0.80, 0.95, 1),
                            size_hint_y=None, height=16)
        self.status = MDLabel(text="in attesa", font_style="Caption",
                              theme_text_color="Custom", text_color=(0.75, 0.82, 0.9, 1))
        self.add_widget(self.title)
        self.add_widget(self.face)
        self.add_widget(self.mood)
        self.add_widget(self.status)

    def set_state(self, text: str, signal: str = "NEUTRAL"):
        self.status.text = text[:42]
        if signal == "LONG":
            self.mood.text = "▲ LONG"
            self.mood.text_color = (0.35, 0.95, 0.55, 1)
            if isinstance(self.face, MDLabel):
                self.face.text_color = (0.35, 0.95, 0.55, 1)
        elif signal == "SHORT":
            self.mood.text = "▼ SHORT"
            self.mood.text_color = (1, 0.38, 0.38, 1)
            if isinstance(self.face, MDLabel):
                self.face.text_color = (1, 0.45, 0.45, 1)
        else:
            self.mood.text = "STANDBY"
            self.mood.text_color = (0.70, 0.80, 0.95, 1)
            if isinstance(self.face, MDLabel):
                self.face.text_color = (0.93, 0.82, 0.42, 1)


class StartScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "start"
        box = MDBoxLayout(orientation="vertical", spacing=10, padding=28)
        box.add_widget(MDLabel(size_hint_y=0.12))
        box.add_widget(MDLabel(text="BLUEBLOOD", halign="center", font_style="H3",
                               theme_text_color="Custom", text_color=(0.93, 0.82, 0.42, 1)))
        box.add_widget(MDLabel(text="WALL STREET FLOOR  •  OPERATIVE HUD",
                               halign="center", theme_text_color="Custom", text_color=(0.55, 0.78, 1, 1)))
        box.add_widget(MDLabel(text="10 agenti  •  missione: crescita del capitale demo",
                               halign="center", theme_text_color="Hint", font_style="Caption"))
        box.add_widget(MDLabel(size_hint_y=0.06))
        box.add_widget(MDRaisedButton(
            text="ENTRA NELLA SALA", pos_hint={"center_x": 0.5},
            size_hint=(None, None), size=(280, 52),
            md_bg_color=(0.16, 0.38, 0.72, 1),
            on_release=self.entra))
        box.add_widget(MDLabel(size_hint_y=0.28))
        self.add_widget(box)

    def entra(self, *_):
        if not self.manager.has_screen("main"):
            self.manager.add_widget(MainScreen())
        self.manager.current = "main"


class MainScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = "main"
        self.engine = Engine()
        self.running = False
        self.busy = False
        self.idx = 0
        self.dialog = None
        self.event = None
        self.desks: Dict[str, DeskCard] = {}

        root = MDBoxLayout(orientation="vertical")
        root.add_widget(MDTopAppBar(title="BLUEBLOOD  //  TRADING FLOOR HUD", elevation=3,
                                    md_bg_color=(0.02, 0.03, 0.07, 1)))

        content = MDBoxLayout(orientation="vertical", spacing=8, padding=10)

        top = MDCard(orientation="vertical", padding=10, spacing=2, size_hint_y=None, height=118,
                     md_bg_color=(0.04, 0.06, 0.11, 1), radius=[4])
        self.cap = MDLabel(text=f"CAPITALE  €{self.engine.capital:.2f}",
                           font_style="H6", halign="center",
                           theme_text_color="Custom", text_color=(0.95, 0.84, 0.40, 1))
        self.pnl = MDLabel(text="P&L +0.00%   •   MISSIONE: PROFITTO",
                           halign="center", theme_text_color="Custom", text_color=(0.35, 0.95, 0.62, 1))
        self.st = MDLabel(text=self._status(), halign="center", theme_text_color="Hint", font_style="Caption")
        top.add_widget(self.cap)
        top.add_widget(self.pnl)
        top.add_widget(self.st)
        self.watch = MDLabel(text="WATCHLIST: in attesa dei prezzi reali",
                             halign="center", theme_text_color="Hint", font_style="Caption")
        top.add_widget(self.watch)
        content.add_widget(top)

        presets = MDBoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=36)
        for amt in (100, 250, 500, 1000):
            presets.add_widget(MDRaisedButton(
                text=f"€{amt}", size_hint_x=0.25,
                md_bg_color=(0.14, 0.22, 0.36, 1),
                on_release=lambda inst, v=amt: self.set_preset(v)))
        content.add_widget(presets)

        ctrl = MDBoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=48)
        self.cap_input = MDTextField(hint_text="Capitale test (es. 100)", text="100",
                                     size_hint_x=0.18, mode="rectangle")
        btn_apply = MDRaisedButton(text="IMPOSTA", size_hint_x=0.12,
                                   md_bg_color=(0.18, 0.40, 0.70, 1), on_release=self.apply_capital)
        btn_reset = MDFlatButton(text="RESET", size_hint_x=0.12,
                                 theme_text_color="Custom", text_color=(1, 0.75, 0.35, 1),
                                 on_release=self.reset_test)
        btn_copy = MDFlatButton(text="COPIA SEGNALE", size_hint_x=0.16,
                                theme_text_color="Custom", text_color=(0.7, 0.85, 1, 1),
                                on_release=self.copy_ticket)
        self.btn_go = MDRaisedButton(text="AVVIA SALA", size_hint_x=0.24,
                                     md_bg_color=(0.10, 0.50, 0.34, 1), on_release=self.start)
        self.btn_stop = MDFlatButton(text="STOP", size_hint_x=0.16,
                                     theme_text_color="Custom", text_color=(1, 0.35, 0.35, 1),
                                     on_release=self.stop)
        for w in (self.cap_input, btn_apply, btn_reset, btn_copy, self.btn_go, self.btn_stop):
            ctrl.add_widget(w)
        content.add_widget(ctrl)

        floor = MDGridLayout(cols=5, spacing=8, size_hint_y=None, height=250)
        for code, name, role, avatar, color in DESKS:
            card = DeskCard(code, name, role, avatar, color, size_hint_y=None, height=128)
            self.desks[code] = card
            floor.add_widget(card)
        content.add_widget(floor)

        self.ticket = MDCard(orientation="vertical", padding=10, spacing=2, size_hint_y=None, height=110,
                             md_bg_color=(0.09, 0.12, 0.18, 1), radius=[10])
        self.t_title = MDLabel(text="Biglietto broker", font_style="Subtitle1",
                               theme_text_color="Custom", text_color=(0.75, 0.84, 0.96, 1),
                               size_hint_y=None, height=22)
        self.t_body = MDLabel(text="La sala è selettiva: pochi trade, solo se gli agenti sono allineati.",
                              theme_text_color="Secondary", font_style="Caption")
        self.pos_label = MDLabel(text="Nessuna posizione aperta.", theme_text_color="Hint", font_style="Caption")
        self.ticket.add_widget(self.t_title)
        self.ticket.add_widget(self.t_body)
        self.ticket.add_widget(self.pos_label)
        content.add_widget(self.ticket)

        tf_row = MDBoxLayout(orientation="horizontal", spacing=6, size_hint_y=None, height=34)
        for tf in ("1m", "5m", "15m"):
            tf_row.add_widget(MDRaisedButton(
                text=tf, size_hint_x=0.33,
                md_bg_color=(0.12, 0.20, 0.34, 1),
                on_release=lambda inst, t=tf: self.set_chart_tf(t)))
        content.add_widget(tf_row)

        info = MDBoxLayout(orientation="horizontal", spacing=8, size_hint_y=None, height=150)
        self.chart_card = MDCard(orientation="vertical", padding=8, spacing=4,
                                 md_bg_color=(0.07, 0.10, 0.16, 1), radius=[10])
        self.chart_lab = MDLabel(text="Grafico live: in attesa dati reali", font_style="Caption",
                                 theme_text_color="Custom", text_color=(0.8, 0.88, 1, 1),
                                 size_hint_y=None, height=18)
        self.price_chart = PriceChart(size_hint_y=1)
        self.chart_card.add_widget(self.chart_lab)
        self.chart_card.add_widget(self.price_chart)
        self.news_card = MDCard(orientation="vertical", padding=8, md_bg_color=(0.07, 0.10, 0.16, 1), radius=[10])
        self.news_lab = MDLabel(text="Notizie: in attesa", font_style="Caption",
                                theme_text_color="Custom", text_color=(0.8, 0.88, 1, 1))
        self.news_card.add_widget(self.news_lab)
        info.add_widget(self.chart_card)
        info.add_widget(self.news_card)
        content.add_widget(info)

        bottom = MDBoxLayout(orientation="horizontal", spacing=8)
        self.log = MDLabel(text="Floor pronta.\nCapitale demo €100.",
                           theme_text_color="Secondary")
        sc = MDScrollView()
        sc.add_widget(self.log)
        self.hist_lab = MDLabel(text="Storico: in attesa di operazioni.",
                                theme_text_color="Secondary", font_style="Caption")
        hist_sc = MDScrollView()
        hist_sc.add_widget(self.hist_lab)
        bottom.add_widget(sc)
        bottom.add_widget(hist_sc)
        content.add_widget(bottom)

        root.add_widget(content)
        self.add_widget(root)
        self.refresh()

    def _status(self) -> str:
        return (f"{'LIVE' if self.running else 'FERMO'}  |  pos {len(self.engine.positions)}/"
                f"{CONFIG['max_open_positions']}  |  trade {self.engine.trades_today}/"
                f"{CONFIG['max_trades_per_day']}  |  W {self.engine.wins} / L {self.engine.losses}")

    def refresh(self):
        p = self.engine.pnl()
        self.cap.text = f"CAPITALE DEMO  €{self.engine.capital:.2f}"
        self.pnl.text = f"P&L {p:+.2f}%   •   obiettivo: crescita controllata"
        self.pnl.text_color = (0.35, 0.90, 0.50, 1) if p >= 0 else (1, 0.36, 0.36, 1)
        self.st.text = self._status()
        if self.engine.positions:
            self.pos_label.text = " | ".join(
                f"{p.symbol} {p.direction} @{p.entry:.2f}" for p in self.engine.positions
            )
        else:
            self.pos_label.text = "Nessuna posizione aperta."
        if self.engine.last_prices:
            self.watch.text = "  ".join(f"{s} {p:.2f}" for s, p in list(self.engine.last_prices.items())[:8])
        else:
            self.watch.text = "WATCHLIST: in attesa dei prezzi reali"

    def add_log(self, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        line = msg if msg.startswith("[") else f"[{now}] {msg}"
        self.log.text = "\n".join((line + "\n" + self.log.text).split("\n")[:14])

    def popup(self, title: str, text: str):
        try:
            from plyer import notification
            notification.notify(title=title, message=text[:160], timeout=8, app_name="BlueBlood")
        except Exception:
            pass
        if self.dialog:
            self.dialog.dismiss()
        self.dialog = MDDialog(
            title=title, text=text, size_hint=(0.86, None),
            buttons=[MDFlatButton(text="OK", theme_text_color="Custom", text_color=(0.35, 0.7, 1, 1),
                                  on_release=lambda *_: self.dialog.dismiss() if self.dialog else None)])
        self.dialog.open()

    def set_chart_tf(self, tf: str):
        CONFIG["chart_tf"] = tf
        self.chart_lab.text = f"Grafico {tf}: aggiornamento live"
        self.add_log(f"Grafico impostato su {tf}")

    def set_preset(self, value: float):
        self.cap_input.text = str(int(value))
        if self.running:
            self.popup("Sala attiva", "Ferma la sala, poi imposta il preset.")
            return
        self.engine.reset(float(value))
        self.refresh()
        self.add_log(f"Preset capitale €{value:.0f}")

    def copy_ticket(self, *_):
        text = self.engine.last_ticket
        if not text:
            self.popup("Nessun segnale", "Ancora nessun biglietto da copiare.")
            return
        try:
            from kivy.core.clipboard import Clipboard
            Clipboard.copy(text)
            self.popup("Copiato", text)
        except Exception:
            self.popup("Segnale", text)

    def apply_capital(self, *_):
        if self.running:
            self.popup("Sala attiva", "Ferma prima la sala, poi cambia il capitale.")
            return
        try:
            val = float(self.cap_input.text.replace(",", "."))
            if val < 20 or val > 100000:
                raise ValueError
        except Exception:
            self.popup("Capitale non valido", "Inserisci un numero tra 20 e 100000.")
            return
        self.engine.reset(val)
        self.refresh()
        self.add_log(f"Capitale impostato a €{val:.2f}")

    def reset_test(self, *_):
        if self.running:
            self.stop()
        try:
            val = float(self.cap_input.text.replace(",", ".") or self.engine.initial)
        except Exception:
            val = 100.0
        self.engine.reset(val)
        for d in self.desks.values():
            d.set_state("reset / in attesa")
        self.t_title.text = "Biglietto broker"
        self.t_body.text = "Test azzerato. La sala riparte da zero."
        self.refresh()
        self.add_log(f"RESET TEST — capitale €{val:.2f}")
        self.popup("Reset eseguito", f"Nuovo test da €{val:.2f}")

    def start(self, *_):
        if self.running:
            return
        self.running = True
        self.btn_go.disabled = True
        self.refresh()
        self.add_log("SALA LIVE — obiettivo profitto sul demo")
        self.event = Clock.schedule_interval(self.tick, float(CONFIG["scan_every_seconds"]))

    def stop(self, *_):
        self.running = False
        self.btn_go.disabled = False
        if self.event:
            self.event.cancel()
            self.event = None
        self.engine.save()
        self.refresh()
        self.add_log("SALA FERMA")

    def tick(self, *_):
        if not self.running or self.busy:
            return
        symbol = CONFIG["symbols"][self.idx % len(CONFIG["symbols"])]
        self.idx += 1
        self.busy = True
        threading.Thread(target=self.worker, args=(symbol,), daemon=True).start()

    def worker(self, symbol: str):
        try:
            res = self.engine.analyze(symbol)
        except Exception as e:
            res = {"events": [], "logs": [f"{symbol} {e}"], "popup": None, "msgs": [], "idea": None, "ok": False}
        Clock.schedule_once(lambda *_: self.apply(res), 0)

    @mainthread
    def apply(self, res: Dict):
        self.busy = False
        for ev in res.get("events", []):
            self.add_log(ev)
            if "TAKE PROFIT" in ev:
                self.popup("TAKE PROFIT", ev)
            elif "STOP" in ev:
                self.popup("STOP LOSS", ev)
        for line in res.get("logs", []):
            self.add_log(line)
        for m in res.get("msgs", []):
            if m.name in self.desks:
                self.desks[m.name].set_state(m.reason, m.signal.value)
        pop = res.get("popup")
        if pop:
            self.popup(pop[0], pop[1])
        idea = res.get("idea")
        if idea:
            self.t_title.text = f"SEGNALE {idea.direction.value}  •  {idea.symbol}"
            self.t_body.text = f"Entry {idea.entry:.4f}   SL {idea.stop:.4f}   TP {idea.tp:.4f}   Size {idea.size_pct*100:.1f}%"
        if res.get("spark"):
            self.chart_lab.text = res.get("chart_name", "") + "  |  " + res["spark"]
        if res.get("closes"):
            self.price_chart.set_data(res["closes"])
        news = res.get("news") or []
        if news:
            self.news_lab.text = "\n".join(news[:3])
        elif res.get("ok"):
            self.news_lab.text = f"{res.get('symbol','')} — nessuna news Yahoo al momento"
        hist = res.get("history")
        if hist:
            self.hist_lab.text = hist
        self.refresh()


class BlueBloodApp(MDApp):
    def build(self):
        global JOURNAL_FILE, STATE_FILE
        try:
            d = self.user_data_dir
            os.makedirs(d, exist_ok=True)
            JOURNAL_FILE = os.path.join(d, "journal.csv")
            STATE_FILE = os.path.join(d, "positions.json")
        except Exception:
            pass
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Blue"
        self.title = "BlueBlood Floor"
        sm = MDScreenManager()
        sm.add_widget(StartScreen())
        return sm


if __name__ == "__main__":
    BlueBloodApp().run()
