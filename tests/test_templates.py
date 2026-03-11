"""Tests for stack template registry."""

from krakenwhip.templates import get_template, get_template_dir, list_templates


def test_list_templates_not_empty():
    templates = list_templates()
    assert len(templates) > 0


def test_get_openclaw_template():
    t = get_template("openclaw")
    assert t is not None
    assert t["name"] == "openclaw"
    assert "ollama" in t["services"]
    assert "qdrant" in t["services"]
    assert "openclaw" in t["services"]
    assert t["tier"] == "free"


def test_get_ollama_template():
    t = get_template("ollama")
    assert t is not None
    assert t["name"] == "ollama"
    assert t["services"] == ["ollama", "open-webui"]
    assert t["tier"] == "free"
    assert t["default_port"] == 3000


def test_get_rag_template():
    t = get_template("rag")
    assert t is not None
    assert t["name"] == "rag"
    assert t["services"] == ["qdrant", "embeddings", "rag-api"]
    assert t["tier"] == "free"
    assert t["default_port"] == 8080


def test_get_unknown_template():
    t = get_template("nonexistent")
    assert t is None


def test_template_dir_exists():
    d = get_template_dir("openclaw")
    assert d.exists()
    assert (d / "docker-compose.yml.j2").exists()


def test_ollama_template_dir_exists():
    d = get_template_dir("ollama")
    assert d.exists()
    assert (d / "docker-compose.yml.j2").exists()


def test_rag_template_dir_exists():
    d = get_template_dir("rag")
    assert d.exists()
    assert (d / "docker-compose.yml.j2").exists()
