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


def test_runs_of_blank_form_rows_fold_into_one_line():
    cells = [[0, 0, 1, 1, True, '이름'], [0, 1, 1, 1, True, '금액'], [1, 0, 1, 1, False, '가'], [1, 1, 1, 1, False, '1'],
             [2, 0, 1, 1, False, ''], [3, 0, 1, 1, False, '나'], [7, 0, 1, 1, False, '합계']]
    html = str(table_html(json.dumps({'n_rows': 12, 'n_cols': 2, 'cells': cells})))
    # 줄 2(빈 줄 하나)는 그대로, 4~6(셋)·8~11(넷)은 한 줄씩으로
    assert html.count('<tr>') == 5 and '빈 줄 3개' in html and '빈 줄 4개' in html and html.count('extract-fold') == 2
    spans = [[0, 0, 4, 1, False, '구분'], [0, 1, 1, 1, False, '값']]
    kept = str(table_html(json.dumps({'n_rows': 4, 'n_cols': 2, 'cells': spans})))
    assert 'extract-fold' not in kept and 'rowspan="4"' in kept                  # 병합 칸이 걸친 줄은 접지 않는다
