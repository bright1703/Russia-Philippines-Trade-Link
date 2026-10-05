"""
Склейка одинаковых новостей: проверка на реальных карточках сайта.

Фикстура — все 57 карточек выпусков 1-5 со страницы
/philippine-trade-news/ (сентябрь-октябрь 2026). Группы ниже размечены
вручную: это одно и то же событие из разных изданий.
"""
import json
import re
from pathlib import Path

from trade_agent.phnews.dedup import (
    cluster, deduplicate, drop_repeats, source_rank,
)

CARDS = json.loads((Path(__file__).parent / "fixtures" / "phnews_site_cards.json")
                   .read_text(encoding="utf-8"))
T = lambda c: c["title"]
X = lambda c: c["extract"]
D = lambda c: c["date"]
RANK = lambda c: (source_rank(c["url"]), -len(c["extract"]))

SAME_EVENT = [
    ["Exports soar to all-time high, curbing trade deficit",
     "DTI pledges support as PH exports hit record $9.11B",
     "Philippines exports hit 35-year high at $9.11B",
     "PH exports soar to record high in August 2026, says PSA",
     "Electronics surge drives PH exports to 35-year high of $9.11B in August",
     "Philippines posts highest product exports in 35 years"],
    ["Philippines inflation likely quickened in September as food and fuel costs rise",
     "Philippine inflation seen accelerating in September as fuel and food costs rise"],
    ["DA backs ‘phased’ pork tariff hikes",
     "DA, stakeholders back phased pork tariff hikes to protect hog raisers"],
    ["PH rice imports seen at 5.7M MT in 2027-USDA",
     "Philippines rice imports may climb to 5.7 million metric tons next year, USDA"],
    ["ADB Boosts Philippines’ Crisis Response with $1.5 Billion",
     "ADB approves $1.5B loan for Philippines’ Middle East crisis response"],
    ["Philippine-EU free trade deal timely as zero tariff regime on some goods to end-DTI chief",
     "Philippines, EU agree on free-trade deal after 10-year push",
     "Philippines, EU reach substantial agreement on landmark free trade deal",
     "Philippines, EU eye FTA signing in 2027",
     "Marcos: PH-EU free trade deal to boost jobs, investments, exports"],
    ["Pork cuts, offal drive PHL meat imports-BAI", "Meat imports up 8% in 8 months"],
    ["ADB, S&P cut Philippines growth forecasts",
     "ADB slashes 2026 PH growth outlook to 3.3% amid high prices"],
    ["Philippines chicken imports seen rising next year",
     "Philippines' chicken imports set to rise 14.7% in 2027"],
]
GROUP_OF = {t: n for n, group in enumerate(SAME_EVENT) for t in group}
# Те же данные BAI под другим углом (MITA): склейка допустима, не обязательна.
OPTIONAL = {"PH meat imports slow despite 8% growth-MITA": GROUP_OF["Meat imports up 8% in 8 months"]}


def _wrong_groups(groups):
    """Группы, где склеены разные события."""
    bad = []
    for group in groups:
        ids = {GROUP_OF.get(c["title"], OPTIONAL.get(c["title"], c["title"])) for c in group}
        if len(ids) > 1:
            bad.append([c["title"] for c in group])
    return bad


def _oracle(prompt):
    """Модель, которая отвечает строго по ручной разметке."""
    found = {}
    for line in prompt.split("Items:\n")[1].splitlines():
        m = re.match(r"(\d+)\. \[[^\]]*\] (.*?) :: ", line)
        if m and m.group(2) in GROUP_OF:
            found.setdefault(GROUP_OF[m.group(2)], []).append(int(m.group(1)))
    return json.dumps({"groups": [v for v in found.values() if len(v) > 1]})


def _run_issues(ask=None):
    """Сборка выпусков по очереди, как в боте: склейка, потом отсев повторов."""
    published, shown = [], 0
    for issue in sorted({c["issue"] for c in CARDS}):
        current = [c for c in CARDS if c["issue"] == issue]
        groups = deduplicate(current, T, X, D, rank=RANK, context=published, ask=ask)
        groups, _ = drop_repeats(groups, published, T, X, D)
        shown += len(groups)
        published += [primary for primary, _ in groups]
    return shown


def test_no_different_events_are_merged():
    assert _wrong_groups(cluster(CARDS, T, X, D)) == []


def test_record_exports_five_outlets_become_one_card():
    issue5 = [c for c in CARDS if c["issue"] == 5]
    groups = deduplicate(issue5, T, X, D, rank=RANK)
    exports = [(p, r) for p, r in groups if "9.11" in p["title"] + p["extract"]]
    assert len(exports) == 1
    primary, rest = exports[0]
    assert len(rest) == 4
    # главной не становится перепечатка msn.com
    assert "msn.com" not in primary["url"]


def test_pork_tariff_story_stages_stay_separate():
    titles = {"Meat traders buck plan to raise pork jowl tariffs",
              "Canada opposes Philippine plan to hike tariff on pork jowls",
              "Meat importers oppose pork tariff hike",
              "DA backs ‘phased’ pork tariff hikes"}
    # Разные стадии одного сюжета (17.09, 27.09, 30.09, 02.10) — отдельные новости.
    cards = [c for c in CARDS if c["title"] in titles]
    assert len(cluster(cards, T, X, D)) == 4


def test_words_only_pipeline_removes_most_duplicates():
    # 57 карточек, по ручной разметке событий 41. Без модели — 45, без ошибок.
    assert _run_issues() <= 45


def test_model_step_catches_reworded_fta_reports():
    assert _run_issues(ask=_oracle) == 41


def test_model_cannot_merge_unrelated_items():
    issue2 = [c for c in CARDS if c["issue"] == 2]
    everything = json.dumps({"groups": [list(range(1, len(issue2) + 1))]})
    groups = deduplicate(issue2, T, X, D, ask=lambda _: everything)
    merged = [[p, *r] for p, r in groups]
    # «Chicken lollipops» (контрабанда) не склеивается с соглашением PH-EU
    lollipops = next(g for g in merged if any("lollipops" in c["title"] for c in g))
    assert not any("EU" in c["title"] for c in lollipops)


def test_broken_model_answer_keeps_word_result():
    issue5 = [c for c in CARDS if c["issue"] == 5]
    plain = deduplicate(issue5, T, X, D)
    for answer in ("not json", '{"groups": "x"}', '{"groups": [["a", 99]]}'):
        assert len(deduplicate(issue5, T, X, D, ask=lambda _, a=answer: a)) == len(plain)

    def failing(_):
        raise TimeoutError

    assert len(deduplicate(issue5, T, X, D, ask=failing)) == len(plain)


def test_same_statistic_a_month_apart_is_not_a_repeat():
    old = {"title": "Philippine inflation seen accelerating in September as fuel and food costs rise",
           "extract": "Inflation is expected to accelerate in September.", "date": "2026-10-04"}
    new = {"title": "Philippine inflation seen accelerating in October as fuel and food costs rise",
           "extract": "Inflation is expected to accelerate in October.", "date": "2026-11-04"}
    fresh, repeats = drop_repeats([(new, [])], [old], T, X, D)
    assert repeats == [] and len(fresh) == 1


def test_source_rank():
    assert source_rank("https://www.pna.gov.ph/articles/1") == 0
    assert source_rank("https://business.inquirer.net/1") == 1
    assert source_rank("https://www.msn.com/en-ph/news/1") == 2
