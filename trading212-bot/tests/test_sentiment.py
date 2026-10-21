import json
from types import SimpleNamespace

from conftest import article
from t212bot.sentiment import ClaudeAnalyzer, LexiconAnalyzer


def test_lexicon_positive_and_negative():
    lex = LexiconAnalyzer()
    pos = lex.assess("AAA", "AAA", [article("AAA beats estimates and raises guidance"),
                                    article("Analyst upgrade for AAA")])
    neg = lex.assess("BBB", "BBB", [article("BBB misses estimates, cuts guidance"),
                                    article("SEC investigation into BBB accounting irregularities")])
    assert pos.sentiment > 0.3 and not pos.red_flags
    assert neg.sentiment < -0.3 and neg.red_flags
    assert lex.assess("C", "C", []).article_count == 0


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def fake_client(text, stop_reason="end_turn"):
    response = SimpleNamespace(stop_reason=stop_reason,
                               content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)])
    beta = FakeMessages(response)
    plain = FakeMessages(response)
    return SimpleNamespace(beta=SimpleNamespace(messages=beta), messages=plain), beta, plain


def test_claude_analyzer_parses_structured_output():
    payload = {"sentiment": 1.7, "catalyst_strength": 0.8, "confidence": 0.9, "red_flags": [],
               "key_points": ["Prognose angehoben"], "summary": "Starke Zahlen."}
    client, beta, _ = fake_client(json.dumps(payload))
    result = ClaudeAnalyzer(client=client).assess("AAA", "AAA Inc.", [article("AAA raises guidance")])
    assert result.analyzer == "claude"
    assert result.sentiment == 1.0  # auf [-1, 1] begrenzt
    assert result.summary == "Starke Zahlen."
    # Request-Form: Modell, strukturierte Ausgabe, serverseitige Fallbacks
    assert beta.kwargs["model"] == "claude-opus-5-5"
    assert beta.kwargs["output_config"]["format"]["type"] == "json_schema"
    assert beta.kwargs["fallbacks"] == "default"
    assert "AAA raises guidance" in beta.kwargs["messages"][0]["content"]


def test_claude_analyzer_uses_plain_endpoint_for_other_models():
    client, beta, plain = fake_client(json.dumps({"sentiment": 0.2, "catalyst_strength": 0.1, "confidence": 0.5,
                                                  "red_flags": [], "key_points": [], "summary": "ok"}))
    ClaudeAnalyzer(model="claude-haiku-5-5", client=client).assess("AAA", "AAA", [article("x")])
    assert plain.kwargs is not None and beta.kwargs is None
    assert "fallbacks" not in plain.kwargs


def test_claude_refusal_falls_back_to_lexicon():
    client, _, _ = fake_client("", stop_reason="refusal")
    result = ClaudeAnalyzer(client=client).assess("AAA", "AAA", [article("AAA beats estimates")])
    assert result.analyzer == "lexicon"


def test_claude_invalid_json_falls_back():
    client, _, _ = fake_client("not json")
    result = ClaudeAnalyzer(client=client).assess("AAA", "AAA", [article("AAA beats estimates")])
    assert result.analyzer == "lexicon"
