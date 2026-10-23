# 웹 한글 기안기 기능 전수조사 (WebHwpCtrl 13.82.0.208)

2026-10-24. 운영 VM(~/opt/webhwp/tomcat/webapps/webhwpctrl)의 기안기 파일을 읽기만 하고, 운영 화면(aura.ync.ac.kr)의 새 문서
(저장하지 않은 빈 문서)에서 동작을 하나씩 실행해 확인했다. 한컴 코드는 저장소에 넣지 않았고 아래에 파일·줄(압축 묶음은 바이트 위치)만 적는다.

목적은 하나다 — 기안기에 이미 있는 기능을 사람과 AURA 가 그대로 쓰게 하고, 우리 화면에 같은 기능을 다시 만들지 않는다
(예: 우리가 만들었던 페이지 설정 창은 기안기 「편집 용지」(F7, `Run('PageSetup')`)와 겹쳐 뺐다).

## 조사한 곳

| 무엇 | 어디 | 내용 |
|---|---|---|
| 공개 API | `js/webhwpctrl.js` (1,221줄) | `HwpCtrl.prototype.*` 메서드 103개와 속성(CharShape 37행, EditMode 53행, PageCount 108행, ParaShape 115행, ParentCtrl 124행, SelectionMode 140행). 주요 메서드: CreateAction 268, GetHeadingString 375, GetFieldList 511, GetTextFile 547, GetPos 574, ShowToolBar 659, ShowRibbon 668, Insert 722, InsertPicture 731, InsertDocument 758, LockCommand 819, ModifyFieldProperties 827, MoveToField 845, PrintDocument 881, PutFieldText 923, Run 931, SaveAs 950, SetTextFile 1003, SetPos 1021, CreatePageImage 1030, FindPrivateInfo 1123, SetMetaTag 1158, EnableZoom 1193 |
| 동작 이름 표 | `js/hwpApp.8c0c55b882433a2897f0.chunk.js` (압축 2줄) 바이트 1,662,446~1,731,388 | 데스크톱 한글과 같은 동작 이름 1,143개(`s[e.A.x]="이름"`). 이름만 있고 웹에 구현이 없는 것이 대부분이다 |
| 실제 등록된 동작 | 같은 묶음 바이트 4,191,450~4,557,945 의 등록 표(`tv[g.A.x]={action:…}`), 4,558,212 의 ActionManager 가 합침 | 467개 + 이름 표에 따로 있는 14개(FileSave 등). `CreateAction(이름)` 은 모르는 이름에도 객체를 돌려주므로 구현 여부는 `.pgi` 가 있는지로 본다. `Run` 은 예외를 삼키고 `undefined` 를 돌려줘 실패를 알 수 없다(webhwpctrl.js 931) |
| 매개변수 묶음 | 같은 묶음 바이트 342,644~346,958 | 이름 있는 묶음 82개와 항목 이름(PageDef·CharShape·ParaShape·TableCreation·TableInsertLine·TableDeleteLine·TableSplitCell·CellBorderFill·HeaderFooter·PageNumPos·FindReplace·ColDef·AutoNum·PageHiding·FootnoteShape·DrawImageAttr…). 테두리 색 항목 이름은 한컴 오타 그대로 `BorderCorlorLeft` |
| 메뉴·단축키 | `js/rights/menu/*.htm`, `js/rights/shortcut/shortcut.htm`, `js/rights/support/restriction.htm` | 리본 탭 파일·편집·보기·입력·서식·쪽·표(·도구), 단축키 일람, 「브라우저 단축키가 우선」 제약 |
| 글꼴 | `WEB-INF/classes/customfont.json`, `commonFrame/font` | localFont 26종 모두 fileName 없음(사용자 PC 글꼴에 기댐), 웹 글꼴 파일은 굴림·돋움·맑은 고딕·HY 몇 종·Spoqa 등 42개 |
| 프록시 오류 | `tomcat/logs/localhost_access_log.*` | `/webhwpctrl/url` 500(주소로 그림 넣기), `/webhwpctrl/open` 500(10-22 오전, 이후 없음), `/webhwpctrl/info` 404 |

## 기능 표

사람 열의 「됨」 은 운영 화면에서 직접 해 본 것, 「미확인」 은 해 보지 않은 것. AURA 열은 이번에 넣은 도구 기준이다
(hwp_action = app/webhwp_native 허용 목록, hwp_live = 예전 허용 목록, 파일 = 서버가 hwpx 를 직접 고치는 도구).

| 기능 | 기안기 길(리본·단축키) | API(동작 id·묶음 항목 / 메서드) | 사람 | AURA |
|---|---|---|---|---|
| 편집 용지(용지·방향·여백·제본) | 쪽 › 편집 용지, F7 | PageSetup (SecDef 묶음, PageDef.PaperWidth·PaperHeight·Landscape·Top/Bottom/Left/RightMargin·HeaderLen·FooterLen·GutterLen·GutterType) | 됨 — F7·`Run('PageSetup')` 둘 다 기안기 창이 뜬다. 우리 창 불필요 | hwp_action PageSetup(mm, paper), 열려 있으면 hwp_page_setup 도 이 동작으로. 닫혀 있으면 파일 |
| 쪽 번호 | 쪽 › 쪽 번호 | PageNumPos (DrawPos 1~10, NumberFormat, SideChar) | 미확인(리본 있음) | hwp_action PageNumPos, hwp_page_number(열려 있으면 기안기) |
| 머리말·꼬리말 | 쪽 › 머리말/꼬리말 | HeaderFooter (HeaderFooterCtrlType 0 머리말·1 꼬리말, Type) → 글 → CloseEx | 됨(API) | hwp_action HeaderFooter{text}, hwp_header_footer |
| 새 번호로 시작 | 쪽 › 새 번호로 시작 | NewNumber (AutoNum: NumType, NewNumber) | Execute true 확인 | hwp_action NewNumber |
| 현재 쪽만 감추기 | 쪽, Ctrl+N,S | PageHiding (Fields 비트) | 미확인 | hwp_action PageHiding |
| 쪽·단·구역 나누기 | Ctrl+Enter · Ctrl+Shift+Enter · Ctrl+Alt+Enter | BreakPage · BreakColumn · BreakSection | 됨(단축키는 브라우저와 겹치지 않음) | hwp_action, 파일 hwp_page_break |
| 다단 | 쪽 › 단 | MultiColumn (ColDef) | 미확인(리본) | 빠짐 — API Execute 가 false(Type·Count 바꿔도 안 됨) |
| 글자 모양 | 서식 › 글자 모양, Alt+L(맥 ⌘L) | CharShape (Bold·Italic·UnderlineType·TextColor·ShadeColor·Height·FaceName*·Spacing*·Ratio*…) | 됨 | hwp_action CharShape(pt·색 이름), 단축 동작 CharShapeBold 등 11개, hwp_text_style(파일) |
| 진하게·기울임·밑줄 | Ctrl+B/I/U(맥 ⌘B/I/U) | CharShapeBold/Italic/Underline | 됨(⌘B 직접 눌러 확인) | hwp_action |
| 글자 색 단축 | Ctrl+M,R 등 | CharShapeTextColorRed 등 8색 | 미확인 | hwp_action CharShape{TextColor} 로 대신 |
| 문단 모양 | 서식 › 문단 모양, Alt+T(맥 ⌘T — 새 탭과 겹침) | ParagraphShape (AlignType 0 양쪽·1 왼쪽·2 오른쪽·3 가운데·4 배분, LineSpacingType·LineSpacing, Left/RightMargin, Indentation, Prev/NextSpacing, KeepWithNext…) | 리본 됨, 맥 ⌘T 는 브라우저가 가져감 | hwp_action ParagraphShape(mm·pt), 정렬·여백·줄 간격 단축 11개 |
| 글머리표·문단 번호·개요 번호 | 서식 › 글머리표 및 문단 번호, Ctrl+K,N | PutBullet · PutParaNumber · PutOutlineNumber · ParaNumberBulletLevelUp/Down | 됨(API) | hwp_action |
| 스타일 | 서식 › 스타일, F6 | Style(묶음 이름이 없어 SetItem 이 듣지 않음) · StyleShortcut1~10 | 됨(목록 1~10번째: 바탕글·본문·개요 1~7…) | hwp_action StyleShortcut1~10, hwp_styles 로 차례 확인. 이름으로 적용은 API 없음 |
| 표 만들기 | 입력 › 표, Ctrl+N,T(Windows 는 새 창과 겹침 → Alt+N,T) | TableCreate (Rows·Cols) | 됨 | hwp_action TableCreate, hwp_table_insert(파일) |
| 줄·칸 추가 | 표 › 줄/칸 추가하기 | TableInsertRowColumn (Side 0 왼쪽·1 오른쪽·2 위·3 아래, Count) | 됨(셈으로 확인) | hwp_action, hwp_live table_row/col |
| 줄·칸 지우기 | 표 › 줄/칸 지우기 | TableDeleteRowColumn (Type 0 칸(열)·1 줄(행)) | 됨(셈으로 확인) | hwp_action |
| 셀 나누기·합치기 | 표, S · M | TableSplitCell (Rows·Cols·DistributeHeight·Merge) · TableCellBlock…TableMergeCell | 됨 | hwp_action(칸 블록 right·down), hwp_table_edit(파일) |
| 셀 테두리·배경 | 표 › 셀 테두리/배경 | CellBorderFill (BorderType*·BorderWidth*·BorderCorlorLeft…·FillAttr.*) | 됨 | hwp_action CellBorderFill{FillColor…} |
| 셀 높이·너비 같게, 세로 정렬 | 표, H · W | TableDistributeCellHeight/Width · TableVAlignTop/Center/Bottom | 미확인 | hwp_action |
| 블록 계산·자릿점 | 표, Ctrl+Shift+S/A/P | TableFormulaSumAuto/AvgAuto/ProAuto · TableInsertComma/DeleteComma | 미확인(Ctrl+Shift+P 는 브라우저와 겹칠 수 있음) | hwp_action |
| 각주·미주 | 입력 › 주석, Ctrl+N,N · Ctrl+N,E | InsertFootnote · InsertEndnote → 글 → CloseEx, 모양은 FootnoteOption | 됨(API) | hwp_action InsertFootnote{text}, hwp_footnote(파일) |
| 그림 | 입력 › 그림, Ctrl+N,I | `InsertPicture(path, embedded, sizeoption, …, width, height, cb)` (webhwpctrl.js 731), 창은 PictureInsertDialog | 내 PC 파일 됨. 웹 주소는 실패 — 기안기 서버가 /webhwpctrl/url 로 받아 오다 500. `data:` 주소는 됨 | hwp_action InsertPicture{image: doc:·file:} — 서버가 고른 그림만, 실행기가 data: 주소로 넣음 |
| 찾기·찾아 바꾸기 | 편집 › 찾기 Ctrl+F · 찾아 바꾸기 Ctrl+H(맥 ⌘⇧H) · 다시 찾기 Ctrl+L | FindDlg · ReplaceDlg · ForwardFind/BackwardFind/AllReplace (FindReplace 묶음) | 리본 됨. 단축키는 브라우저 찾기·기록·주소창과 겹침 — 아래 「사람 쪽」 | hwp_action AllReplace(알림 없이), 자리 {find} 는 ForwardFind, hwp_replace(파일) |
| 맞춤법 | 도구(검토) › 맞춤법, F8 | SpellingCheck | 됨 — 창이 뜨고 고칠 말(단축→줄임)을 낸다 | 없음(사람이 고르는 창) |
| 글 넣기 | — | InsertText (Text) · PutFieldText | 됨 | hwp_action InsertText, hwp_fill·hwp_write |
| 되돌리기 | Ctrl+Z | Undo · Redo | 됨 | API 로는 듣지 않아 판으로(hwp_undo) |
| 인쇄 | 파일 › 인쇄, Ctrl+P | Print · `PrintDocument()` (881) | 미확인. Ctrl+P 는 브라우저 인쇄(화면 틀째)가 먼저 | 없음(허용 안 함) |
| 저장·다른 이름·내려받기 | 파일 | FileSave · FileSaveAs · `SaveAs` (950) · `GetTextFile('HWPX')` | 우리 저장 단추·Ctrl+S 가 GetTextFile 로 | 없음(서버 판·내려받기 경로로) |
| 하이퍼링크·웹 동영상 | 입력, Ctrl+K,H | InsertHyperlink · InsertMovie | 미확인(외부 주소) | 없음(외부 주소라 허용 안 함) |
| 수식·차트·도형·글상자 | 입력 | EquationCreate · InsertChart · DrawObjCreator* | 미확인 | 없음 |
| 메모·개인정보 찾기 | — | InsertFieldMemo · FindPrivateInfo (1123) | 미확인 | 없음 |
| 보기(쪽 맞춤·폭 맞춤·조판 부호·문단 부호) | 보기, Ctrl+G,P/B/C/T | ViewZoomFitPage/FitWidth · ViewOptionCtrlMark/ParaMark | 됨(리본). Ctrl+G 는 우리 화면이 가로채면 안 됨 | 필요 없음 |
| 읽기 — 커서·블록 글·모양 | — | GetPos · GetTextFile('TEXT','saveblock') · CharShape · ParaShape · GetHeadingString · PageCount · ParentCtrl.CtrlID | — | hwp_inspect(읽기만, 담당자 커서 그대로) |

없는 기능: 차례(목차) 만들기 — 동작 이름 표에도 없다(TableOfContents·MakeContents 미등록). 문서 끼워 넣기(InsertFile·`Insert`)는
있지만 파일 경로를 받아 열지 않았다.

## AURA 가 이제 할 수 있는 것

- hwp_action — 위 표의 「hwp_action」 동작 73개(허용 목록 app/webhwp_native.NATIVE). 인자는 한컴 항목 이름 그대로, 단위만 mm·pt·#RRGGBB/색 이름.
  범위 밖·모르는 항목은 고치지 않고 거절한다(조용히 자르지 않음). 자리는 필드·찾은 글(n번째)·표 칸 블록·문서 처음/끝만 — 사람 커서 자리에는 쓰지 않는다.
- hwp_inspect — 열린 기안기에서 위치·필드·블록 글·글자/문단 모양·개요 번호·쪽 수·표 안인지·필드 목록·문서 글(상한 2만 자).
- hwp_styles — 작업본 스타일 차례(StyleShortcut 번호와 같은 차례).
- 기안기가 열려 있으면 hwp_page_setup·hwp_page_number·hwp_header_footer 가 파일 대신 기안기 동작(PageSetup·PageNumPos·HeaderFooter)으로 —
  기안기 창이 하는 일과 같고 화면이 다시 불러오지 않는다. 시작 번호·정렬처럼 동작으로 못 옮기는 인자는 파일 도구로.
- 안전: 동작·항목 허용 목록, 값 범위, 글 2,000자·단계 200개·그림 8MB·읽기 결과 2만 자 상한, 임의 스크립트 없음(단계 원소 8가지만, 서버와
  실행기 양쪽에서 거름), 공동 편집 임대를 다른 사람이 쥔 칸·문서 구조에는 보내지 않음, 명령은 대화 담당자의 화면이 먼저 가져감.

## 사람 쪽에서 고칠 것

1. 찾기 단축키 — 지금 편집 화면은 Ctrl+S 만 잡는다. 기안기 도움말대로 브라우저 단축키가 먼저라 Ctrl/⌘+F 는 브라우저 찾기,
   Ctrl+H 는 브라우저 기록(Windows)이 뜬다. Ctrl/⌘+F → FindDlg, Ctrl+H·⌘⇧H → ReplaceDlg 만 잇는다. F3(블록 설정)·Ctrl+G(보기 묶음 단축키)는
   기안기 것이라 가로채면 안 된다(예전 편집 화면이 F3·Ctrl+G 를 다시 찾기로 가로챘다).
2. 막을 수 없는 브라우저 단축키 — Ctrl+N(새 창)·Ctrl+T(새 탭)·Ctrl+W, 맥 ⌘T(문단 모양)·⌘L(주소창). 기안기 대체 길(Alt+N,T · Alt+T · 리본)을
   도움말에 한 줄로 둔다.
3. 인쇄 — Ctrl/⌘+P 는 편집 화면 틀째 인쇄된다. 기안기 Print 로 잇거나 PDF 내려받기로 안내(Print 동작은 아직 미확인).
4. 그림 웹 주소·웹 동영상 — 기안기 서버가 주소를 받아 오다 실패(/webhwpctrl/url 500). 내 PC 그림은 된다. 쓰지 못하게 하려면 `LockCommand`.
5. 글꼴 — customfont.json 의 26종이 모두 사용자 PC 글꼴에 기댄다. 휴먼명조·HY 계열·한컴바탕이 없는 PC(특히 맥)는 대체 글꼴로 줄바꿈이 달라진다.
   쓸 수 있는 웹 글꼴 파일을 기안기 글꼴 설정에 더하는 것은 라이선스 확인 뒤 서버 설정 일.
6. 새 문서 화면에서 저장 창(새 문서 만들기)이 한 번 저절로 떴다(문서를 누르고 단축키를 시험하던 중, 원인 미확인). 그 창의 폴더 나무 글자가 겹쳐 보였다.

편집 화면(hwp_edit.html)에 넣을 바꿈은 공동 편집 작업과 겹쳐 직접 고치지 않았다 — 실행기 한 줄 넣기·명령 실행 위임·찾기 단축키는 작업 보고에 패치로 적었다.
