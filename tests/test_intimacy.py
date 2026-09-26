"""Tests for the intimacy / relationship-stage system."""
from __future__ import annotations

import intimacy


def test_compute_intimacy_zero():
    assert intimacy.compute_intimacy(0) == 0


def test_compute_intimacy_caps_at_100():
    assert intimacy.compute_intimacy(100) == 100
    assert intimacy.compute_intimacy(1000) == 100


def test_compute_intimacy_scales():
    assert intimacy.compute_intimacy(5) == 15
    assert intimacy.compute_intimacy(10) == 30


def test_render_intimacy_prompt_stranger():
    prompt = intimacy.render_intimacy_prompt(0)
    assert "刚认识" in prompt
    assert "亲密度: 0/100" in prompt


def test_render_intimacy_prompt_deep_love():
    prompt = intimacy.render_intimacy_prompt(90)
    assert "深爱" in prompt
    assert "亲密度: 90/100" in prompt


def test_render_intimacy_prompt_familiar():
    prompt = intimacy.render_intimacy_prompt(40)
    assert "熟悉" in prompt