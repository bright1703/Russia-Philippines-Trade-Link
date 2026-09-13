"""
Таксономия: отрасли, товарные группы и типы событий.

Главное правило этого модуля — отрасль и тип события не одно и то же.
Рыба, лес и косметика — отрасли (товарные группы). Пошлина, тендер,
логистика, изменение спроса — типы событий или факторы влияния. Раньше
и то и другое лежало в одном поле `category`, из-за чего «логистика»
одновременно означала и отрасль перевозчика, и фактор для рыбозавода.

Совпадение ключевого слова ищется по границам слова (см. `evidence`).
Голая подстрока «порт» внутри «экспорт» и «транспорт» отраслью компании
не делает — из-за неё в рабочей базе 126 компаний получили LOGISTICS.

HS-коды здесь — ПОДСКАЗКИ на уровне товарной группы, а не классификация.
Точный код всегда подтверждается человеком.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import (
    CATEGORIES, DIRECTION_BOTH, DIRECTION_PH_TO_RU, DIRECTION_RU_TO_PH,
    DIRECTION_UNKNOWN, EVENT_BUSINESS_CONTACT, EVENT_BUYER_REQUEST, EVENT_DEMAND,
    EVENT_LOGISTICS, EVENT_MARKET_ACCESS, EVENT_OTHER, EVENT_RULE_CHANGE,
    EVENT_SUPPLY, EVENT_TYPES, TRADE_DIRECTIONS,
)
from . import evidence

# --- отрасли и товарные группы ---------------------------------------------
# Дерево начинается с реального каталога Приморского края: рыба, пищевая
# продукция и АПК, лес, промышленные товары, плюс заметные отдельные группы.
SECTOR_FISH = "FISH_SEAFOOD"
SECTOR_FOOD = "FOOD_AGRI"
SECTOR_FOOD_MEAT = "FOOD_MEAT"
SECTOR_FOOD_DAIRY = "FOOD_DAIRY"
SECTOR_FOOD_HONEY = "FOOD_HONEY"
SECTOR_FOOD_BEVERAGE = "FOOD_BEVERAGE"
SECTOR_FOOD_CONFECTIONERY = "FOOD_CONFECTIONERY"
SECTOR_FOOD_GRAIN = "FOOD_GRAIN"
SECTOR_AGRI_INPUTS = "AGRI_INPUTS"
SECTOR_TIMBER = "TIMBER"
SECTOR_INDUSTRIAL = "INDUSTRIAL"
SECTOR_IND_MACHINERY = "IND_MACHINERY"
SECTOR_IND_AUTO = "IND_AUTO"
SECTOR_IND_PACKAGING = "IND_PACKAGING"
SECTOR_IND_ENERGY = "IND_ENERGY"
SECTOR_COSMETICS = "COSMETICS"
SECTOR_LOGISTICS = "LOGISTICS_SERVICES"
SECTOR_CREATIVE = "CREATIVE"
SECTOR_OTHER = "OTHER"

# Подгруппа наследует связь родителя: новость про пищевую продукцию
# касается и медовой компании, но не наоборот.
SECTOR_PARENTS: dict[str, Optional[str]] = {
    SECTOR_FISH: None,
    SECTOR_FOOD: None,
    SECTOR_FOOD_MEAT: SECTOR_FOOD,
    SECTOR_FOOD_DAIRY: SECTOR_FOOD,
    SECTOR_FOOD_HONEY: SECTOR_FOOD,
    SECTOR_FOOD_BEVERAGE: SECTOR_FOOD,
    SECTOR_FOOD_CONFECTIONERY: SECTOR_FOOD,
    SECTOR_FOOD_GRAIN: SECTOR_FOOD,
    SECTOR_AGRI_INPUTS: SECTOR_FOOD,
    SECTOR_TIMBER: None,
    SECTOR_INDUSTRIAL: None,
    SECTOR_IND_MACHINERY: SECTOR_INDUSTRIAL,
    SECTOR_IND_AUTO: SECTOR_INDUSTRIAL,
    SECTOR_IND_PACKAGING: SECTOR_INDUSTRIAL,
    SECTOR_IND_ENERGY: SECTOR_INDUSTRIAL,
    SECTOR_COSMETICS: None,
    SECTOR_LOGISTICS: None,
    SECTOR_CREATIVE: None,
    SECTOR_OTHER: None,
}
SECTORS = tuple(SECTOR_PARENTS)

SECTOR_LABELS: dict[str, str] = {
    SECTOR_FISH: "рыба и морепродукты",
    SECTOR_FOOD: "пищевая продукция и АПК",
    SECTOR_FOOD_MEAT: "мясо и мясопродукты",
    SECTOR_FOOD_DAIRY: "молочная продукция",
    SECTOR_FOOD_HONEY: "мёд и продукты пчеловодства",
    SECTOR_FOOD_BEVERAGE: "напитки",
    SECTOR_FOOD_CONFECTIONERY: "кондитерские изделия",
    SECTOR_FOOD_GRAIN: "зерно и продукты переработки",
    SECTOR_AGRI_INPUTS: "средства производства для АПК",
    SECTOR_TIMBER: "лес и деревообработка",
    SECTOR_INDUSTRIAL: "промышленные товары",
    SECTOR_IND_MACHINERY: "оборудование и машины",
    SECTOR_IND_AUTO: "транспортные средства и запчасти",
    SECTOR_IND_PACKAGING: "упаковка и тара",
    SECTOR_IND_ENERGY: "энергоносители",
    SECTOR_COSMETICS: "косметика и бытовая химия",
    SECTOR_LOGISTICS: "транспортно-логистические услуги",
    SECTOR_CREATIVE: "креативные индустрии",
    SECTOR_OTHER: "отрасль не определена",
}

# Ключевые слова отрасли. Ищутся по границам слова с учётом словоформ.
SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    SECTOR_FISH: (
        "рыба", "рыбный", "рыбопродукция", "рыбопереработка", "минтай", "треска",
        "сельдь", "лосось", "горбуша", "кета", "нерка", "краб", "креветка",
        "кальмар", "гребешок", "трепанг", "икра", "морепродукты", "марикультура",
        "аквакультура", "fish", "fishery", "fisheries", "seafood", "pollock",
        "cod", "herring", "salmon", "crab", "shrimp", "squid", "scallop",
        "mariculture", "aquaculture", "canned fish", "bfar",
    ),
    SECTOR_FOOD: (
        "продовольствие", "пищевая", "пищевой", "продукты питания", "еда",
        "консервы", "полуфабрикаты", "масложировая", "растительное масло",
        "соевое масло", "food", "foodstuff", "foodstuffs", "processed food",
        "canned", "edible oil", "агропромышленный", "сельское хозяйство",
        "agriculture", "agricultural", "agri", "farm", "livestock", "harvest",
    ),
    SECTOR_FOOD_MEAT: (
        "мясо", "мясной", "свинина", "говядина", "птица", "курятина",
        "субпродукты", "тушка", "убой", "халяль", "meat", "pork", "beef",
        "poultry", "chicken", "offal", "carcass", "slaughter", "halal",
        "африканская чума", "african swine fever", "asf",
    ),
    SECTOR_FOOD_DAIRY: (
        "молоко", "молочный", "сыр", "сливочное масло", "мороженое", "творог",
        "dairy", "milk", "cheese", "butter", "ice cream", "yoghurt",
    ),
    SECTOR_FOOD_HONEY: ("мед", "меда", "медовый", "пчеловодство", "honey",
                        "beekeeping", "propolis", "прополис"),
    SECTOR_FOOD_BEVERAGE: (
        "напиток", "напитки", "вода питьевая", "минеральная вода", "пиво",
        "квас", "сок", "лимонад", "beverage", "beverages", "drinks",
        "bottled water", "mineral water", "beer", "juice", "soft drink",
    ),
    SECTOR_FOOD_CONFECTIONERY: (
        "кондитерский", "конфеты", "шоколад", "печенье", "вафли", "пряники",
        "confectionery", "candy", "chocolate", "biscuit", "cookies", "wafer",
    ),
    SECTOR_FOOD_GRAIN: (
        "зерно", "зерновые", "пшеница", "кукуруза", "ячмень", "соя", "соевый",
        "рис", "мука", "крупа", "комбикорм", "шрот", "grain", "wheat", "corn",
        "maize", "barley", "soybean", "soybeans", "rice", "flour", "milling",
        "feed", "meal",
    ),
    SECTOR_AGRI_INPUTS: (
        "удобрение", "удобрения", "карбамид", "аммиачная селитра", "селитра",
        "калий", "фосфат", "агрохимия", "средства защиты растений",
        "fertilizer", "fertiliser", "urea", "ammonium", "potash", "npk",
        "phosphate", "agrochemical", "pesticide", "fpa",
    ),
    SECTOR_TIMBER: (
        "лесоматериалы", "пиломатериалы", "древесина", "лесопромышленный",
        "шпон", "фанера", "брус", "доска обрезная", "щепа", "пеллеты",
        "timber", "lumber", "sawn wood", "sawnwood", "plywood", "veneer",
        "wood", "wood products", "pellets", "logs",
    ),
    SECTOR_INDUSTRIAL: (
        "промышленный", "металлопрокат", "металлоконструкции", "сталь",
        "прокат", "трубы", "цемент", "стройматериалы", "химическая продукция",
        "industrial", "steel", "metal", "pipes", "cement", "construction materials",
        "chemicals",
    ),
    SECTOR_IND_MACHINERY: (
        "оборудование", "станок", "станки", "двигатель", "двигатели",
        "генератор", "насос", "компрессор", "котел", "турбина", "судовые двигатели",
        "machinery", "equipment", "engine", "engines", "generator", "pump",
        "compressor", "boiler", "turbine", "marine engine", "spare parts",
    ),
    SECTOR_IND_AUTO: (
        "автомобиль", "автомобили", "грузовик", "автобус", "запчасти", "шины",
        "спецтехника", "vehicle", "vehicles", "automotive", "truck", "bus",
        "tire", "tyres",
    ),
    SECTOR_IND_PACKAGING: (
        "упаковка", "тара", "гофрокартон", "паллеты", "этикетка",
        "packaging", "packing", "carton", "pallet", "label",
    ),
    SECTOR_IND_ENERGY: (
        "уголь", "нефтепродукты", "дизель", "мазут", "топливо", "сжиженный газ",
        "coal", "petroleum", "diesel", "fuel oil", "lng", "lpg", "energy",
    ),
    SECTOR_COSMETICS: (
        "косметика", "косметический", "парфюмерия", "бытовая химия",
        "средства гигиены", "cosmetics", "cosmetic", "perfume", "toiletries",
        "personal care", "household chemicals",
    ),
    SECTOR_LOGISTICS: (
        "логистика", "перевозка", "перевозки", "экспедирование", "фрахт",
        "контейнер", "контейнерный", "порт", "терминал", "судоходство",
        "рефрижератор", "холодная цепь", "склад", "logistics", "shipping",
        "freight", "cargo", "container", "terminal", "vessel", "cold chain",
        "warehouse", "port call",
    ),
    SECTOR_CREATIVE: (
        "креативные индустрии", "дизайн", "анимация", "видеопродакшн",
        "разработка игр", "creative industries", "design studio", "animation",
    ),
}

# Подсказки HS на уровне групп (2–4 знака).
SECTOR_HS_HINTS: dict[str, tuple[str, ...]] = {
    SECTOR_FISH: ("03", "0303", "0304", "0306", "0307", "1604", "1605"),
    SECTOR_FOOD: ("16", "19", "20", "21", "15"),
    SECTOR_FOOD_MEAT: ("02", "0201", "0202", "0203", "0206", "0207", "1602"),
    SECTOR_FOOD_DAIRY: ("04", "0401", "0402", "0405", "0406"),
    SECTOR_FOOD_HONEY: ("0409",),
    SECTOR_FOOD_BEVERAGE: ("22", "2201", "2202", "2203"),
    SECTOR_FOOD_CONFECTIONERY: ("17", "1704", "1806", "1905"),
    SECTOR_FOOD_GRAIN: ("10", "11", "12", "1001", "1005", "1101", "1201", "23"),
    SECTOR_AGRI_INPUTS: ("31", "3102", "3104", "3105", "38"),
    SECTOR_TIMBER: ("44", "4403", "4407", "4412"),
    SECTOR_INDUSTRIAL: ("72", "73", "25", "28", "29", "39"),
    SECTOR_IND_MACHINERY: ("84", "85", "8408", "8413", "8502"),
    SECTOR_IND_AUTO: ("87", "8703", "8704", "8708", "4011"),
    SECTOR_IND_PACKAGING: ("48", "4819", "3923"),
    SECTOR_IND_ENERGY: ("27", "2701", "2710", "2711"),
    SECTOR_COSMETICS: ("33", "3303", "3304", "3305", "3401"),
    SECTOR_LOGISTICS: (),
    SECTOR_CREATIVE: (),
    SECTOR_OTHER: (),
}

# --- отрасли исходного каталога -------------------------------------------
# Исходная отрасль каталога — отправная точка, а не гарантия точности.
CATALOG_INDUSTRY_SECTORS: dict[str, tuple[str, ...]] = {
    "рыба и морепродукты": (SECTOR_FISH,),
    "агропромышленный комплекс": (SECTOR_FOOD,),
    "лесопромышленный комплекс": (SECTOR_TIMBER,),
    "промышленный экспорт": (SECTOR_INDUSTRIAL,),
    "креативные индустрии": (SECTOR_CREATIVE,),
    "транспортно-логистический комплекс": (SECTOR_LOGISTICS,),
}

# --- типы событий ----------------------------------------------------------
EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    EVENT_RULE_CHANGE: (
        "пошлина", "тариф", "квота", "запрет", "ограничение", "сертификат",
        "сертификация", "аккредитация", "регистрация", "стандарт", "регламент",
        "административный приказ", "постановление", "приказ",
        "tariff", "duty", "duties", "quota", "ban", "restriction", "certificate",
        "accreditation", "registration", "administrative order",
        "memorandum order", "circular", "regulation",
    ),
    EVENT_MARKET_ACCESS: (
        "допуск", "реестр предприятий", "разрешение на ввоз", "import permit",
        "market access", "eligible establishments", "approved establishments",
        "инспекция предприятия", "аттестация",
    ),
    EVENT_DEMAND: (
        "спрос", "потребление", "импорт вырос", "закупает", "дефицит",
        "demand", "consumption", "shortage", "imports rose", "imports increased",
    ),
    EVENT_SUPPLY: (
        "производство выросло", "урожай", "вылов", "предложение", "экспорт вырос",
        "harvest", "catch", "output", "production", "supply", "surplus",
    ),
    EVENT_LOGISTICS: (
        "фрахт", "ставка фрахта", "маршрут", "рейс", "судозаход", "задержка",
        "контейнерная линия", "freight rate", "route", "shipping line",
        "congestion", "delay", "cold chain",
    ),
    EVENT_BUYER_REQUEST: (
        "тендер", "закупка", "конкурс", "заявка", "приглашение к участию",
        "invitation to bid", "request for quotation", "procurement", "bidding",
        "bid notice", "philgeps",
    ),
    EVENT_BUSINESS_CONTACT: (
        "бизнес-миссия", "деловая миссия", "делегация", "переговоры", "встреча",
        "выставка", "форум", "меморандум о сотрудничестве", "соглашение о намерениях",
        "торгпредство", "business mission", "delegation", "trade mission",
        "memorandum of understanding", "business forum", "exhibition",
        "trade fair", "b2b",
    ),
}

# --- юрисдикции ------------------------------------------------------------
JURISDICTION_PH = "PH"
JURISDICTION_RU = "RU"
JURISDICTION_EAEU = "EAEU"

JURISDICTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    JURISDICTION_PH: (
        "филиппины", "филиппинский", "манила", "philippines", "philippine",
        "manila", "dti", "bfar", "bai", "fda philippines", "bureau of customs",
        "tariff commission", "philgeps", "department of agriculture",
        "bureau of animal industry", "bureau of plant industry",
    ),
    JURISDICTION_RU: (
        "россия", "российский", "рф", "russia", "russian", "фтс",
        "россельхознадзор", "роспотребнадзор", "минсельхоз", "минпромторг",
        "приморье", "приморский край", "владивосток",
    ),
    JURISDICTION_EAEU: (
        "еаэс", "еэк", "евразийский экономический союз", "таможенный союз",
        "eaeu", "eurasian economic union",
    ),
}

# Третьи страны нужны, чтобы отличить «чужое» правило от нашего.
THIRD_COUNTRY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "CN": ("китай", "китайский", "china", "chinese"),
    "ZA": ("юар", "южная африка", "south africa"),
    "VN": ("вьетнам", "vietnam"),
    "IN": ("индия", "india"),
    "US": ("сша", "united states", "usa"),
    "EU": ("евросоюз", "европейский союз", "european union"),
    "JP": ("япония", "japan"),
    "KR": ("корея", "korea"),
    "ID": ("индонезия", "indonesia"),
    "TH": ("таиланд", "thailand"),
    "NO": ("норвегия", "norway"),
}

# --- совместимость со старым полем category --------------------------------
# Старые записи в базе и старые тесты пользуются плоским списком CATEGORIES.
CATEGORY_SECTORS: dict[str, tuple[str, ...]] = {
    "MEAT": (SECTOR_FOOD_MEAT,),
    "FOOD": (SECTOR_FOOD, SECTOR_FISH),
    "AGRICULTURE": (SECTOR_FOOD,),
    "FERTILIZER": (SECTOR_AGRI_INPUTS,),
    "GRAIN": (SECTOR_FOOD_GRAIN,),
    "AUTO": (SECTOR_IND_AUTO,),
    "EQUIPMENT": (SECTOR_IND_MACHINERY,),
    "PACKAGING": (SECTOR_IND_PACKAGING,),
    "ENERGY": (SECTOR_IND_ENERGY,),
    "LOGISTICS": (SECTOR_LOGISTICS,),
    "REGULATION": (),
    "TENDER": (),
    "IMPORT": (),
    "OTHER": (),
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "MEAT": SECTOR_KEYWORDS[SECTOR_FOOD_MEAT],
    "FOOD": SECTOR_KEYWORDS[SECTOR_FOOD] + SECTOR_KEYWORDS[SECTOR_FISH]
            + SECTOR_KEYWORDS[SECTOR_FOOD_DAIRY],
    "AGRICULTURE": ("сельское хозяйство", "агропромышленный", "аграрный",
                    "фермер", "урожай", "животноводство", "аквакультура",
                    "agriculture", "agricultural", "farm", "crop", "harvest",
                    "livestock", "aquaculture", "fisheries"),
    "FERTILIZER": SECTOR_KEYWORDS[SECTOR_AGRI_INPUTS],
    "GRAIN": SECTOR_KEYWORDS[SECTOR_FOOD_GRAIN],
    "AUTO": SECTOR_KEYWORDS[SECTOR_IND_AUTO],
    "EQUIPMENT": SECTOR_KEYWORDS[SECTOR_IND_MACHINERY],
    "PACKAGING": SECTOR_KEYWORDS[SECTOR_IND_PACKAGING],
    "ENERGY": SECTOR_KEYWORDS[SECTOR_IND_ENERGY],
    "LOGISTICS": SECTOR_KEYWORDS[SECTOR_LOGISTICS] + ("таможня", "customs"),
    "REGULATION": EVENT_KEYWORDS[EVENT_RULE_CHANGE] + EVENT_KEYWORDS[EVENT_MARKET_ACCESS],
    "TENDER": EVENT_KEYWORDS[EVENT_BUYER_REQUEST],
    "IMPORT": ("импорт", "импортер", "ввоз", "внешнеторговый оборот",
               "import", "importation", "importer", "trade volume",
               "supplier country"),
}

CATEGORY_HS_HINTS: dict[str, tuple[str, ...]] = {
    category: tuple(dict.fromkeys(
        code for sector in sectors for code in SECTOR_HS_HINTS.get(sector, ())))
    for category, sectors in CATEGORY_SECTORS.items()
}
CATEGORY_HS_HINTS["FOOD"] = ("03", "04", "16", "19", "20", "21", "0303", "0304")

# Явный шум: такие материалы отбрасываются предфильтром.
NOISE_MARKERS = (
    "job vacancy", "hiring", "scholarship", "birthday", "condolence",
    "happy holidays", "webinar registration", "photo release",
    "basketball", "beauty pageant", "raffle", "розыгрыш призов",
    "вакансия", "поздравляем с днем рождения",
)


# --- поиск -----------------------------------------------------------------
def normalize(text: str) -> str:
    """Нормализация текста (см. `evidence.fold`)."""
    return evidence.fold(text)


def _score_dictionary(text: str, dictionary: dict[str, tuple[str, ...]],
                      limit: int) -> list[tuple[str, int]]:
    folded = evidence.fold(text)
    scores: list[tuple[str, int]] = []
    for key, keywords in dictionary.items():
        hits = 0
        for keyword in keywords:
            pattern = evidence.term_regex(keyword)
            if pattern is not None and pattern.search(folded):
                hits += 1
        if hits:
            scores.append((key, hits))
    scores.sort(key=lambda pair: (-pair[1], pair[0]))
    return scores[:limit] if limit else scores


def guess_sectors(text: str, limit: int = 3) -> list[tuple[str, int]]:
    """Возвращает [(отрасль, число совпадений)] по убыванию."""
    return _score_dictionary(text, SECTOR_KEYWORDS, limit)


def guess_event_types(text: str, limit: int = 3) -> list[tuple[str, int]]:
    """Возвращает [(тип события, число совпадений)] по убыванию."""
    return _score_dictionary(text, EVENT_KEYWORDS, limit)


def guess_categories(text: str, limit: int = 3) -> list[tuple[str, int]]:
    """Старое плоское поле category. Оставлено для совместимости."""
    return _score_dictionary(text, CATEGORY_KEYWORDS, limit)


def sector_evidence(text: str, sectors: Iterable[str],
                    limit: int = 4) -> list[evidence.TermHit]:
    """Фрагменты текста, подтверждающие отраслевую принадлежность."""
    terms: list[str] = []
    for sector in sectors:
        terms.extend(SECTOR_KEYWORDS.get(sector, ()))
    return evidence.find_terms(text, terms, limit=limit)


def detect_jurisdictions(text: str) -> list[str]:
    """Юрисдикции, упомянутые в материале: чьё правило может меняться."""
    return [code for code, _ in _score_dictionary(text, JURISDICTION_KEYWORDS, 0)]


def detect_third_countries(text: str) -> list[str]:
    return [code for code, _ in _score_dictionary(text, THIRD_COUNTRY_KEYWORDS, 0)]


def sector_chain(sector: str) -> list[str]:
    """Отрасль вместе с родительскими уровнями."""
    chain: list[str] = []
    current: Optional[str] = sector
    while current and current not in chain:
        chain.append(current)
        current = SECTOR_PARENTS.get(current)
    return chain


def sectors_overlap(event_sectors: Iterable[str],
                    company_sectors: Iterable[str]) -> list[str]:
    """
    Пересечение отраслей события и компании.

    Связь засчитывается только по прямой линии родства: отрасли совпали,
    либо одна является более широкой группой для другой. Новость про
    пищевую продукцию касается компании с мёдом, а компания с широкой
    отраслью «пищевая продукция и АПК» получает связь с новостью про мясо.

    Общий предок связью НЕ является: удобрения и мясо оба относятся
    к АПК, но новость про мясо производителя удобрений не касается.
    """
    company_list = list(company_sectors)
    result: list[str] = []
    for event in event_sectors:
        event_chain = sector_chain(event)
        for company in company_list:
            if event in sector_chain(company) or company in event_chain:
                if event not in result:
                    result.append(event)
                break
    return result


def hs_hints(categories: Iterable[str]) -> list[str]:
    """Подсказки HS по категории или отрасли."""
    hints: list[str] = []
    for key in categories:
        codes = SECTOR_HS_HINTS.get(key) or CATEGORY_HS_HINTS.get(key, ())
        for code in codes:
            if code not in hints:
                hints.append(code)
    return hints


def sector_hs_hints(sectors: Iterable[str]) -> list[str]:
    hints: list[str] = []
    for sector in sectors:
        for level in sector_chain(sector):
            for code in SECTOR_HS_HINTS.get(level, ()):
                if code not in hints:
                    hints.append(code)
    return hints


def looks_like_noise(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in NOISE_MARKERS)


def valid_category(value: str) -> str:
    upper = (value or "").strip().upper()
    return upper if upper in CATEGORIES else "OTHER"


def valid_sector(value: str) -> str:
    upper = (value or "").strip().upper()
    return upper if upper in SECTORS else SECTOR_OTHER


def valid_sectors(values: Iterable[str], limit: int = 4) -> list[str]:
    result: list[str] = []
    for value in values or ():
        sector = valid_sector(str(value))
        if sector != SECTOR_OTHER and sector not in result:
            result.append(sector)
        if len(result) >= limit:
            break
    return result


def valid_event_type(value: str) -> str:
    upper = (value or "").strip().upper()
    return upper if upper in EVENT_TYPES else EVENT_OTHER


def valid_direction(value: str) -> str:
    upper = (value or "").strip().upper()
    return upper if upper in TRADE_DIRECTIONS else DIRECTION_UNKNOWN


def sector_label(sector: str) -> str:
    return SECTOR_LABELS.get(sector, sector)


def sectors_from_industry(industry: str) -> list[str]:
    """Отрасли по колонке «Отрасль экспорта» исходного каталога."""
    key = evidence.normalize(industry)
    for name, sectors in CATALOG_INDUSTRY_SECTORS.items():
        if evidence.normalize(name) == key:
            return list(sectors)
    return []


__all__ = [
    "SECTORS", "SECTOR_KEYWORDS", "SECTOR_LABELS", "SECTOR_PARENTS",
    "SECTOR_HS_HINTS", "CATALOG_INDUSTRY_SECTORS", "EVENT_KEYWORDS",
    "JURISDICTION_KEYWORDS", "JURISDICTION_PH", "JURISDICTION_RU",
    "JURISDICTION_EAEU", "THIRD_COUNTRY_KEYWORDS", "CATEGORY_KEYWORDS",
    "CATEGORY_HS_HINTS", "CATEGORY_SECTORS", "NOISE_MARKERS",
    "guess_sectors", "guess_event_types", "guess_categories", "sector_evidence",
    "detect_jurisdictions", "detect_third_countries", "sector_chain",
    "sectors_overlap", "hs_hints", "sector_hs_hints", "looks_like_noise",
    "valid_category", "valid_sector", "valid_sectors", "valid_event_type",
    "valid_direction", "sector_label", "sectors_from_industry", "normalize",
    "DIRECTION_RU_TO_PH", "DIRECTION_PH_TO_RU", "DIRECTION_BOTH",
    "DIRECTION_UNKNOWN",
]
