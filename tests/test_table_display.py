import json

from zzaimy.app.render import table_html


def test_sparse_cells_keep_original_column_positions():
    html = str(table_html(json.dumps({
        'n_rows': 1, 'n_cols': 4,
        'cells': [[0, 1, 1, 1, False, '두 번째 열']],
    })))
    assert '<tr><td></td><td>두 번째 열</td><td></td><td></td></tr>' in html


def test_rowspan_and_colspan_do_not_create_extra_cells():
    html = str(table_html(json.dumps({
        'n_rows': 2, 'n_cols': 3, 'col_w': [.2, .3, .5],
        'cells': [[0, 0, 2, 1, True, '구분'], [0, 1, 1, 2, True, '내용'],
                  [1, 2, 1, 1, False, '첫 줄\n둘째 줄 <검수>']],
    })))
    assert '<col style="width:20.00%;">' in html
    assert '<col style="width:30.00%;">' in html
    assert '<col style="width:50.00%;">' in html
    assert 'rowspan="2"' in html and 'colspan="2"' in html
    assert '<tr><td></td><td>첫 줄\n둘째 줄 &lt;검수&gt;</td></tr>' in html


def test_trailing_blank_form_rows_fold_but_inner_blanks_stay():
    cells = [[0, 0, 1, 1, True, '이름'], [0, 1, 1, 1, True, '금액'], [1, 0, 1, 1, False, '가'], [1, 1, 1, 1, False, '1'],
             [2, 0, 1, 1, False, ''], [3, 0, 1, 1, False, '나']]
    html = str(table_html(json.dumps({'n_rows': 10, 'n_cols': 2, 'cells': cells})))
    assert html.count('<tr>') == 4 and '빈 줄 6개는 접었습니다' in html         # 가운데 빈 줄(2)은 그대로
    few = str(table_html(json.dumps({'n_rows': 6, 'n_cols': 2, 'cells': cells})))
    assert few.count('<tr>') == 6 and '접었습니다' not in few                   # 둘 이하는 접지 않는다
