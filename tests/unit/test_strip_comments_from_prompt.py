from utils.prompt import strip_comments_from_prompt

def test_strip_comments_from_prompt_preserves_edge_cases():
    assert strip_comments_from_prompt(None) is None
    assert strip_comments_from_prompt('') == ''
    assert strip_comments_from_prompt('<!--') == '<!--'
    assert strip_comments_from_prompt('-->') == '-->'
    assert strip_comments_from_prompt('<!-->') == '<!-->'
    assert strip_comments_from_prompt('<!--->') == '<!--->'

def test_strip_comments_from_prompt_removes_inline_comments():
    prompt = 'Keep <!-- remove --> this. <!-- remove too --> End.'

    assert strip_comments_from_prompt(prompt) == 'Keep  this.  End.'

def test_strip_comments_from_prompt_removes_multiline_comments():
    prompt = 'Before\n<!--\nml comment\n-->After'

    assert strip_comments_from_prompt(prompt) == 'Before\nAfter'

def test_strip_comments_from_prompt_removes_multiline_comment_and_trailing_newline():
    prompt = '<!--l2\nl3\nl4-->\nAfter'

    assert strip_comments_from_prompt(prompt) == 'After'

def test_strip_comments_from_prompt_removes_indented_multiline_comment():
    prompt = '   <!--\n comment \n -->'

    assert strip_comments_from_prompt(prompt) == '   '

def test_strip_comments_from_prompt_ignores_indented_multiline_comment_with_more_than_three_spaces():
    prompt = '    <!--\n foo -->'

    assert strip_comments_from_prompt(prompt) == prompt

def test_strip_comments_from_prompt_supports_empty_comments():
    prompt = 'A<!---->B\n<!--\n-->C<!---->D'

    assert strip_comments_from_prompt(prompt) == 'AB\nCD'

def test_strip_comments_from_prompt_does_not_support_nested_comments():
    prompt = '<!-- start <!--nested--> end -->'

    assert strip_comments_from_prompt(prompt) == ' end -->'
