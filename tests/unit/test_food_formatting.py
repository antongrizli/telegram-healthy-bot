from html import unescape
from html.parser import HTMLParser

import pytest

from src.presenters.food import format_draft_card_text, format_food_analysis
from src.utils.i18n_locales import LOCALES


ANALYSIS = {
    'food_items': [{'name': 'Питайя', 'portion': '150 г', 'calories': 90,
                    'protein': 1.8, 'fat': 0.7, 'carb': 18.8}],
    'total_calories': 90, 'total_protein': 1.8, 'total_fat': 0.7, 'total_carb': 18.8,
}


class BoldHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.open_tags = []
        self.bold = []

    def handle_starttag(self, tag, attrs):
        assert tag == 'b' and not attrs
        self.open_tags.append(tag)

    def handle_endtag(self, tag):
        assert self.open_tags.pop() == tag

    def handle_data(self, text):
        if self.open_tags:
            self.bold.append(text)


@pytest.mark.parametrize('lang', list(LOCALES))
@pytest.mark.parametrize('draft', [True, False])
def test_food_templates_render_as_bold_html_in_all_locales(lang, draft):
    text = (format_draft_card_text({'analysis': ANALYSIS, 'meal_type': 'lunch'}, lang)
            if draft else format_food_analysis(ANALYSIS, lang))
    assert '*' not in text
    parser = BoldHTML()
    parser.feed(text)
    assert not parser.open_tags
    assert len(parser.bold) >= 6
    assert 'Питайя' in text and '150 г' in text and '90' in text


@pytest.mark.parametrize('draft', [True, False])
def test_model_or_user_text_cannot_inject_markup(draft):
    item = {**ANALYSIS['food_items'][0], 'name': 'A_*[B] <b>еда</b> & C',
            'portion': '<150 г> & 1/2'}
    analysis = {**ANALYSIS, 'food_items': [item]}
    text = (format_draft_card_text({'analysis': analysis}, 'ru')
            if draft else format_food_analysis(analysis, 'ru'))
    assert '&lt;b&gt;еда&lt;/b&gt;' in text
    assert '&lt;150 г&gt; &amp; 1/2' in text
    assert item['name'] in unescape(text)
    parser = BoldHTML()
    parser.feed(text)
    assert not parser.open_tags
