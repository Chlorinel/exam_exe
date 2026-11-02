from decimal import Decimal
from html.parser import HTMLParser
import unittest

from deepseek_question_generator import GeneratedQuestion
from question_review_gui_with_generation import (
    _simple_markdown_to_html,
    build_question_preview_html,
)


class PreviewParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.tags = []

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)


def parse_preview(markup):
    parser = PreviewParser()
    parser.feed(markup)
    parser.close()
    return parser


class QuestionPreviewTests(unittest.TestCase):
    def test_math_survives_html_parsing(self):
        cases = [
            # A less-than sign followed by a letter starts an HTML tag.
            r"\(|t|<T_0/4\), \(T_0/4<|t|<T_0/2\). Find X_k. [30352]",
            r"\[\begin{cases}1 & t<T_0/4 \\ -1 & t>T_0/4\end{cases}\]",
            r'\(a<b, c>d, a&b, \text{"quoted"}, &lt;\) trailing text',
            r"\(\text{<img src=x>}\) trailing text",
        ]
        for source in cases:
            with self.subTest(source=source):
                parsed = parse_preview(_simple_markdown_to_html(source))
                self.assertEqual("".join(parsed.text), source)
                self.assertEqual(parsed.tags, ["p"])

    def test_markdown_does_not_change_protected_math(self):
        source = r"**Condition:** \(a_b*c<d\) and `x<y`"
        parsed = parse_preview(_simple_markdown_to_html(source))
        self.assertEqual("".join(parsed.text), r"Condition: \(a_b*c<d\) and x<y")
        self.assertEqual(parsed.tags, ["p", "strong", "code"])

    def test_all_question_fields_keep_their_complete_text(self):
        stem = r"设周期方波 \(x(t)=1\)（\(|t|<T_0/4\)），求傅里叶系数。[30352]"
        option = r"\(X_k<T_0\) 选项末尾"
        answer = r"\(a<b\) 答案末尾"
        explanation = r"由 \(a<b<c\) 得到结论，解析末尾。"
        question = GeneratedQuestion(
            local_id="Q001",
            question_type="single_choice",
            chapter="第四章",
            knowledge_point="方波傅里叶变换",
            difficulty="medium",
            stem=stem,
            options={"A": option},
            answer=answer,
            explanation=explanation,
            score=Decimal("5"),
        )
        original = question.to_dict()
        text = "".join(parse_preview(build_question_preview_html(question)).text)
        for value in (stem, option, answer, explanation):
            self.assertIn(value, text)
        self.assertEqual(question.to_dict(), original)


if __name__ == "__main__":
    unittest.main()
