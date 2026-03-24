"""FTS5 trigram モード用テキスト前処理。
trigram tokenize はSQLite FTS5が内部で行うため、
外部トークナイズは不要。クエリ用ストップワード除去のみ提供。"""


STOPWORDS = frozenset([
    "の", "に", "は", "を", "た", "が", "で", "て", "と", "し",
    "れ", "さ", "ある", "いる", "も", "する", "から", "な", "こと",
    "として", "い", "や", "れる", "など", "なっ", "ない", "この",
    "ため", "その", "あっ", "よう", "また", "もの", "という", "あり",
    "まで", "られ", "なる", "へ", "か", "だ", "これ", "です", "ます",
])


def prepare_text(text: str) -> str:
    """FTS5 インデックス用: テキストをそのまま返す（trigramはDB側で処理）"""
    return text.strip()


def prepare_query(text: str) -> str:
    """FTS5 検索クエリ用: trigramモードでは生テキストでMATCH"""
    return text.strip()
