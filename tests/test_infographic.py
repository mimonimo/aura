"""도식 그리기 — 상자·요점 spec 을 PNG 로. 글자 크기·상자 수에 따라 크기가 정해지고 내용이 들어간다."""

import io

from PIL import Image

from zzaimy.app import infographic


def test_render_cards_and_flow_produce_png_with_content():
    spec = {"title": "국가 정책 동향과 대학의 대응", "layout": "cards",
            "blocks": [{"title": "국가 정책 동향", "items": ["'AI 3대 강국' 도약과 전 산업 AI 내재화", "100조원 규모 투자"]},
                       {"title": "지역 산업 수요", "items": ["대구 5대 미래신산업(D5) 육성", "제조·물류·서비스의 AX 확산"]},
                       {"title": "대학 여건", "items": ["AI-X추진단 총장 직속", "VISION2030"]},
                       {"title": "대응 방향", "items": ["전 학과 X+AI 교육", "산학 연계 실무 교육"]}],
            "footer": "AI 역량을 갖춘 전문기술인재 양성"}
    png = infographic.render(spec)
    im = Image.open(io.BytesIO(png))
    assert im.format == "PNG" and im.width == infographic.W and im.height > 300
    px = im.convert("L").load()
    dark = sum(1 for y in range(0, im.height, 4) for x in range(0, im.width, 4) if px[x, y] < 128)
    assert dark > 500                                                    # 글자·상자가 그려졌다
    flow = infographic.render({"title": "추진 단계", "layout": "flow", "blocks": [{"title": f"{i}단계", "items": ["할 일"]} for i in (1, 2, 3)]})
    im2 = Image.open(io.BytesIO(flow))
    assert im2.height < im.height                                        # 한 줄 흐름은 낮다
    assert infographic.parse_spec('{"title": "t", "blocks": [{"title": "a", "items": ["b"]}]}')["blocks"][0]["title"] == "a"
    assert infographic.parse_spec("그냥 글") is None
