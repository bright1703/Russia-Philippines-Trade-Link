"""
Склейка одинаковых новостей из разных источников.

Один факт (рекорд экспорта, соглашение PH-EU, кредит ADB) перепечатывают
5-6 изданий с разными заголовками. Совпадение URL или хеша текста таких
перепечаток не ловит, поэтому сравниваем смысловые слова заголовка и
начала выдержки плюс «якорные» числа ($9.11B, 5.7M MT, 27.8%).

Два шага:
1. Слова и числа (без сети, мгновенно). Порог строгий: на пяти выпусках
   сайта ни одной ложной склейки.
2. Необязательно: модель смотрит список заголовков и называет группы
   одного события. Нужна для случаев вроде PH-EU FTA, где пять изданий
   пишут об одном соглашении совсем разными словами. Модель только
   добавляет склейки, и только между карточками с хоть каким-то общим
   словом; при ошибке ответа остаётся результат шага 1.

Модуль не зависит от остального кода: на вход идут словари или объекты,
поля задаются функциями-аксессорами.
"""
from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any, Callable, Iterable, Optional, Sequence

# Слова, которые есть почти в каждой новости ленты и ничего не различают.
_STOP = set("""
a an the and or of to in on at for by with from as into over amid after before
is are was were be been being has have had will would may might could can
says said say according report reports reported new next last this that these
its it their than more most up down out about against via per year years month
months week weeks percent pct mln million billion bn usd us dollar dollars php
philippines philippine ph phl pilipinas filipino filipinos country nation local
government gov govt official officials data
и в на по за от до из для что как это его их при об не но уже ещё году года
филиппины филиппин филиппинах филиппинский филиппинская
""".split())

# Синонимы приводятся к одному слову до сравнения.
_PHRASES = [
    (r"free[\s-]+trade[\s-]+(agreement|deal|pact|accord)s?", " fta "),
    (r"european union", " eu "),
    (r"asian development bank", " adb "),
    (r"department of agriculture", " da "),
    (r"bureau of animal industry", " bai "),
    (r"bureau of customs", " boc "),
    (r"philippine statistics authority", " psa "),
    (r"department of trade and industry", " dti "),
    (r"united states department of agriculture|us department of agriculture", " usda "),
    (r"bangko sentral( ng pilipinas)?", " bsp "),
    (r"african swine fever", " asf "),
    (r"metric tons?|tonnes?", " mt "),
    (r"all[\s-]time high|35[\s-]year high|record[\s-]high|highest", " record "),
    (r"\bhikes?\b|\bincreases?\b|\braises?\b|\bclimbs?\b|\bsoars?\b|\bsurges?\b|\bjumps?\b|\brises?\b|\brising\b", " rise "),
    (r"\bcuts?\b|\bslash(es|ed)?\b|\blowers?\b|\btrims?\b", " cut "),
    (r"\boutlook\b|\bforecasts?\b|\bprojections?\b", " forecast "),
    (r"\bshipments?\b|\boutbound\b", " export "),
]

_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|percent|b\b|bn\b|billion|m\b|mn\b|million|k\b)?",
                  re.IGNORECASE)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _stem(word: str) -> str:
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)] + repl
    return word


def anchors(text: str) -> set[str]:
    """Различающие числа: 9.11, 27.8%, 5.7. Годы и голые малые целые не в счёт."""
    found = set()
    for match in _NUM.finditer(_fold(text).replace(",", "")):
        value, unit = match.group(1), (match.group(2) or "").lower()
        number = float(value)
        if not unit and "." not in value:
            # 2026, 8 months, 20 consecutive: слишком частые, якорем не служат
            continue
        if 1900 <= number <= 2100 and "." not in value:
            continue
        found.add(f"{number:g}")
    return found


def words(text: str) -> set[str]:
    text = _fold(text)
    for pattern, repl in _PHRASES:
        text = re.sub(pattern, repl, text)
    result = set()
    for token in re.findall(r"[a-zа-яё][a-zа-яё']*[a-zа-яё]", text):
        token = token.replace("'s", "").strip("'")
        if len(token) < 2 or token in _STOP:
            continue
        result.add(_stem(token))
    return result


class Fingerprint:
    __slots__ = ("title", "body", "nums")

    def __init__(self, title: str, extract: str = "", lead_words: int = 30):
        self.title = words(title)
        lead = " ".join((extract or "").split()[:lead_words])
        self.body = self.title | words(lead)
        self.nums = anchors(f"{title} {lead}")


class Weights:
    """
    Вес слова по редкости (IDF) в текущей подборке.

    «EU» и «FTA» редкие и решают дело, «import» и «tariff» есть в каждой
    второй новости ленты и почти ничего не значат.
    """

    def __init__(self, prints: Iterable["Fingerprint"]):
        prints = list(prints)
        self.size = len(prints)
        self.df: dict[str, int] = {}
        for fp in prints:
            for token in fp.body:
                self.df[token] = self.df.get(token, 0) + 1

    def __call__(self, tokens: set[str]) -> float:
        n = self.size
        return sum(math.log((n + 1) / (self.df.get(t, 0) + 1)) + 1 for t in tokens)


def _dice(a: set[str], b: set[str], weight: Weights) -> float:
    if not a or not b:
        return 0.0
    return 2 * weight(a & b) / (weight(a) + weight(b))


def similarity(a: "Fingerprint", b: "Fingerprint", weight: Weights) -> float:
    """
    Среднее взвешенных совпадений заголовка и начала текста, 0..1.

    Общее различающее число ($9.11B, 5.7M MT) добавляет немного: само по
    себе «8%» ничего не доказывает.
    """
    score = (_dice(a.title, b.title, weight) + _dice(a.body, b.body, weight)) / 2
    if a.nums & b.nums:
        score += 0.1
    return score


# Порог выбран по пяти выпускам сайта (57 карточек, сентябрь-октябрь 2026):
# самая похожая пара РАЗНЫХ новостей набрала 0.28 («Meat importers oppose
# pork tariff hike» и «DA backs phased pork tariff hikes»).
THRESHOLD = 0.34


def _as_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if "T" in text else text[:10], fmt).date()
        except ValueError:
            continue
    return None


def cluster(items: Sequence[Any],
            title: Callable[[Any], str],
            extract: Callable[[Any], str] = lambda _: "",
            when: Callable[[Any], Any] = lambda _: None,
            window_days: int = 7,
            context: Sequence[Any] = (),
            threshold: float = THRESHOLD) -> list[list[Any]]:
    """
    Группы одинаковых новостей, в исходном порядке.

    context — прошлые карточки: участвуют только в расчёте весов слов,
    чтобы на маленькой подборке частые слова не казались редкими.
    Пары дальше window_days друг от друга не склеиваются: «инфляция за
    сентябрь» и «инфляция за октябрь» похожи словами, но это разные события.
    """
    prints = [Fingerprint(title(it), extract(it)) for it in items]
    weight = Weights(prints + [Fingerprint(title(it), extract(it)) for it in context])
    dates = [_as_date(when(it)) for it in items]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if dates[i] and dates[j] and abs((dates[i] - dates[j]).days) > window_days:
                continue
            if similarity(prints[i], prints[j], weight) >= threshold:
                parent[find(j)] = find(i)

    groups: dict[int, list[Any]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


# Кого показывать главной карточкой группы: первоисточник важнее
# перепечатки, msn.com и подобные агрегаторы — в последнюю очередь.
PRIMARY_DOMAINS = ("gov.ph", "adb.org", "worldbank.org", "usda.gov", "fao.org")
AGGREGATOR_DOMAINS = ("msn.com", "news.google.", "yahoo.com", "tradersunion.com",
                      "canadianinquirer.net")


def source_rank(url: str) -> int:
    """0 — первоисточник, 1 — обычное издание, 2 — агрегатор-перепечатка."""
    host = re.sub(r"^https?://(www\.)?", "", (url or "").lower()).split("/")[0]
    if any(host == d or host.endswith("." + d) or host.endswith(d) for d in PRIMARY_DOMAINS):
        return 0
    if any(d in host for d in AGGREGATOR_DOMAINS):
        return 2
    return 1


def deduplicate(items: Sequence[Any],
                title: Callable[[Any], str],
                extract: Callable[[Any], str] = lambda _: "",
                when: Callable[[Any], Any] = lambda _: None,
                rank: Optional[Callable[[Any], Any]] = None,
                window_days: int = 7,
                context: Sequence[Any] = (),
                ask: Optional[Callable[[str], str]] = None) -> list[tuple[Any, list[Any]]]:
    """
    Одна карточка на событие: [(главная, [перепечатки]), ...].

    rank: какая карточка главная (меньше — лучше), например
    lambda it: (source_rank(it.url), -len(it.summary)). По умолчанию
    главной остаётся первая по списку, то есть порядок отбора сохраняется.
    ask: функция «промпт -> ответ модели» для второго шага; None — без модели.
    Порядок групп — по первой карточке группы во входном списке.
    """
    groups = cluster(items, title, extract, when, window_days, context)
    if ask is not None and len(groups) > 1:
        groups = _llm_merge(groups, title, extract, when, ask, context)
    result = []
    for group in groups:
        ordered = sorted(group, key=rank) if rank else list(group)
        result.append((ordered[0], ordered[1:]))
    return result


def already_published(item: Any, published: Sequence[Any],
                      title: Callable[[Any], str],
                      extract: Callable[[Any], str] = lambda _: "",
                      when: Callable[[Any], Any] = lambda _: None,
                      window_days: int = 10,
                      threshold: float = THRESHOLD) -> Optional[Any]:
    """
    Карточка прошлых выпусков с тем же событием, если такая есть.

    Так «экспорт за август — рекорд» не выходит и 1 октября, и 5 октября.
    Продолжение сюжета (пошлина на свинину: сначала DA, потом Канада, потом
    импортёры) сюда не попадает: это разные события с разными словами.
    """
    mine = Fingerprint(title(item), extract(item))
    prints = [Fingerprint(title(old), extract(old)) for old in published]
    weight = Weights(prints + [mine])
    my_date = _as_date(when(item))
    best, best_score = None, threshold
    for old, fp in zip(published, prints):
        old_date = _as_date(when(old))
        if my_date and old_date and abs((my_date - old_date).days) > window_days:
            continue
        score = similarity(mine, fp, weight)
        if score >= best_score:
            best, best_score = old, score
    return best


def drop_repeats(groups: Sequence[tuple[Any, list[Any]]], published: Sequence[Any],
                 title: Callable[[Any], str],
                 extract: Callable[[Any], str] = lambda _: "",
                 when: Callable[[Any], Any] = lambda _: None,
                 window_days: int = 10) -> tuple[list[tuple[Any, list[Any]]], list[tuple[Any, Any]]]:
    """
    Убирает группы, событие которых уже было в прошлых выпусках.

    Проверяется вся группа: из пяти заметок о рекорде экспорта с прошлым
    выпуском напрямую совпала только одна, но повтор — все пять.
    Возвращает (оставшиеся группы, [(главная карточка, прошлая карточка)]).
    """
    fresh, repeats = [], []
    for primary, rest in groups:
        old = None
        for member in [primary, *rest]:
            old = already_published(member, published, title, extract, when, window_days)
            if old is not None:
                break
        if old is None:
            fresh.append((primary, rest))
        else:
            repeats.append((primary, old))
    return fresh, repeats


# --- шаг 2: модель -------------------------------------------------------

# Склейку от модели принимаем, только если у пары есть хоть какое-то общее
# содержание. Самая слабая настоящая пара на сайте (PH-EU FTA) набрала 0.17.
LLM_FLOOR = 0.12

LLM_PROMPT = """Below is a numbered list of news items for a Philippine trade news digest.
Several outlets often report the SAME event (one statistics release, one deal,
one loan approval, one official statement) under different headlines.

Group the items that report the same event. Rules:
- Same event = same underlying fact or announcement, even if the angle or the
  quoted person differs (e.g. "PH, EU reach FTA deal" and "Marcos: PH-EU FTA
  to boost jobs" are the same event).
- NOT the same event: different stages of one story (a proposal, then a
  protest against it, then a decision), different months of the same
  statistic, different countries or products.
- When unsure, do not group.

Answer with JSON only, no text around it:
{{"groups": [[1, 4, 7], [2, 5]]}}
List only groups of two or more items. If there are none: {{"groups": []}}

Items:
{items}
"""


def _llm_merge(groups: list[list[Any]],
               title: Callable[[Any], str],
               extract: Callable[[Any], str],
               when: Callable[[Any], Any],
               ask: Callable[[str], str],
               context: Sequence[Any]) -> list[list[Any]]:
    import json

    heads = [g[0] for g in groups]
    lines = []
    for n, it in enumerate(heads, 1):
        lead = " ".join((extract(it) or "").split()[:40])
        stamp = _as_date(when(it))
        lines.append(f"{n}. [{stamp or 'no date'}] {title(it)} :: {lead}")
    try:
        answer = ask(LLM_PROMPT.format(items="\n".join(lines)))
        start, end = answer.index("{"), answer.rindex("}") + 1
        proposed = json.loads(answer[start:end]).get("groups") or []
    except Exception:
        return groups  # ответ модели не разобран — остаётся шаг 1

    prints = [Fingerprint(title(it), extract(it)) for it in heads]
    weight = Weights(prints + [Fingerprint(title(it), extract(it)) for it in context])
    parent = list(range(len(groups)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for group in proposed:
        if not isinstance(group, list):
            continue
        idx = sorted({int(n) - 1 for n in group
                      if isinstance(n, (int, str)) and str(n).isdigit()
                      and 1 <= int(n) <= len(heads)})
        for a in idx:
            for b in idx:
                if a < b and similarity(prints[a], prints[b], weight) >= LLM_FLOOR:
                    parent[find(b)] = find(a)

    merged: dict[int, list[Any]] = {}
    for i, group in enumerate(groups):
        merged.setdefault(find(i), []).extend(group)
    return list(merged.values())
