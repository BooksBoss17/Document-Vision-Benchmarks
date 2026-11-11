from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor/report-content-metrics'))
import random
import unittest
from content_score_c06 import alignment, normalize, legacy


class ExactContent(unittest.TestCase):
    def test_vectorized_exact_objective_matches_independent_legacy_dp(self):
        rng=random.Random(20261111)
        alphabet=[("a",()),("b",()),("中",()),("1",("sub",)),("1",("sup",)),("/",())]
        for _ in range(1200):
            a=[rng.choice(alphabet) for _ in range(rng.randrange(18))]
            b=[rng.choice(alphabet) for _ in range(rng.randrange(18))]
            self.assertEqual(alignment(a,b),legacy.alignment(a,b))

    def test_prefix_suffix_and_repeated_ties(self):
        for a,b in [("ab","ba"),("ababab","bababa"),("prefix_x_suffix","prefix_y_suffix"),("", "abc")]:
            x=[(c,()) for c in a];y=[(c,()) for c in b]
            self.assertEqual(alignment(x,y),legacy.alignment(x,y))

    def test_table_format_is_not_visible_text(self):
        a=normalize('<table><tr><td>A</td><td>21</td></tr><tr><td>B</td><td>34</td></tr></table>',"TABLE_HTML")
        b=normalize('| A | 21 |\n|---|---|\n| B | 34 |',"TABLE_HTML")
        self.assertEqual(a["units"],b["units"])

    def test_span_layout_digits_do_not_become_content(self):
        a=normalize(r'\begin{tabular}{cc}\toprule\multicolumn{2}{c}{A_1} & 21\\\bottomrule\end{tabular}',"TABLE_HTML")
        b=normalize('<table><tr><td>A<sub>1</sub></td><td>21</td></tr></table>',"TABLE_HTML")
        self.assertEqual(a["units"],b["units"])

    def test_math_bars_inside_markdown_table_cells(self):
        a=normalize('<table><tr><td>$|x|$</td><td>`a|b`</td></tr></table>',"TABLE_HTML")
        b=normalize('| $|x|$ | `a|b` |\n|---|---|',"TABLE_HTML")
        self.assertEqual(a["units"],b["units"])

    def test_one_column_markdown_table(self):
        a=normalize('<table><tr><td>A</td></tr><tr><td>B</td></tr></table>',"TABLE_HTML")
        b=normalize('| A |\n|---|\n| B |',"TABLE_HTML")
        self.assertEqual(a["units"],b["units"])

    def test_absolute_bars_and_wrong_exponents_survive(self):
        self.assertIn(("|",()),normalize('|x|',"TABLE_HTML")["units"])
        self.assertNotEqual(normalize('a_1',"LATEX")["units"],normalize('a^1',"LATEX")["units"])



class GlyphCompatibility(unittest.TestCase):
    def test_same_rendered_tex_and_unicode_glyphs(self):
        for tex,visible in [(r'a_1+\ldots+b^2','a₁+…+b²'),(r'\ell','ℓ'),(r'\hbar','ℏ')]:
            a,b=normalize(tex,'LATEX'),normalize(visible,'LATEX')
            self.assertTrue(a['supported'] and b['supported'])
            self.assertEqual(a['units'],b['units'])
        self.assertNotEqual(normalize(r'a_1+\ldots+b^2','LATEX')['units'],normalize('a₂+…+b²','LATEX')['units'])

    def test_expansion_preserves_roles_and_atomic_structural_tokens(self):
        from content_score_c06 import fold_glyph_units
        self.assertEqual(fold_glyph_units([('<accent>',('struct',)),('…',('sub',))]),
                         [('<accent>',('struct',)),('.',('sub',)),('.',('sub',)),('.',('sub',))])
        self.assertNotEqual(normalize('a_1','LATEX')['units'],normalize('a^1','LATEX')['units'])


class ExistingAliasConsistency(unittest.TestCase):
    def test_direct_unicode_uses_existing_tex_alias_policy(self):
        for tex,unicode in [(r'\cdot','⋅'),(r'\sim','∼'),(r'\mid','∣'),(r'\perp','⟂'),(r'\cdots','⋯'),(r'\varsigma','ς')]:
            self.assertEqual(normalize(tex,'LATEX')['units'],normalize(unicode,'LATEX')['units'])

    def test_different_operators_and_roles_still_differ(self):
        for a,b in [('⋅','×'),('∼','≈'),('∣','‖'),('⟂','∥'),('⋯','⋮'),('ς','ρ'),('a_1','a^1')]:
            self.assertNotEqual(normalize(a,'LATEX')['units'],normalize(b,'LATEX')['units'])

    def test_all_supported_existing_symbol_mappings_are_symmetric(self):
        count=0
        for name,value in legacy.SYMBOLS.items():
            a,b=normalize(name,'LATEX'),normalize(value,'LATEX')
            if a['supported'] and b['supported']:
                self.assertEqual(a['units'],b['units'],name);count+=1
        self.assertEqual(count,104)


if __name__=="__main__":
    unittest.main()
