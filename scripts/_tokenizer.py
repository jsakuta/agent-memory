import fugashi

STOPWORDS = frozenset([
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し",
    "れ", "さ", "ある", "いる", "も", "する", "から", "な", "こと",
    "として", "い", "や", "れる", "など", "なっ", "ない", "この",
    "ため", "その", "あっ", "よう", "また", "もの", "という", "あり",
    "まで", "られ", "なる", "へ", "か", "だ", "これ", "です", "ます",
])

_tagger = None

def get_tagger() -> fugashi.Tagger:
    global _tagger
    if _tagger is None:
        _tagger = fugashi.Tagger('-Owakati')
    return _tagger

def tokenize(text: str) -> str:
    """FTS5 インデックス用: 分かち書きのみ（ストップワード除去なし）"""
    return get_tagger().parse(text).strip()

def tokenize_query(text: str) -> list[str]:
    """FTS5 検索クエリ用: 分かち書き + ストップワード除去"""
    tokens = get_tagger().parse(text).strip().split()
    return [t for t in tokens if t not in STOPWORDS and len(t) > 0]
