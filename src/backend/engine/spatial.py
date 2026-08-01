def analyze_space(model_id: str):

    # 現在はテスト解析
    # 将来的にArchicad MCPデータ解析へ置換

    result = {

        "model_id": model_id,

        "elements": {

            "walls": 120,
            "doors": 18,
            "windows": 35,
            "rooms": 10

        },

        "issues": [

            {
                "type": "warning",
                "message": "廊下幅チェック未実施"
            }

        ]

    }

    return result