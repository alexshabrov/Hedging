class Params:
    # Networks data
    NETWORKS = {
        "arbitrum": {
            "rpc_url_template": "https://arbitrum-mainnet.infura.io/v3/{RPC_KEY}",
            "ws_url_template": "wss://arbitrum-mainnet.infura.io/ws/v3/{RPC_KEY}",
            "stables": [
                "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
            ],
            "npm": "0xC36442b4a4522E871399CD717aBDD847Ab11FE88",
        },
        "ethereum": {
            "rpc_url_template": "https://mainnet.infura.io/v3/{RPC_KEY}",
            "ws_url_template": "wss://mainnet.infura.io/ws/v3/{RPC_KEY}",
            "stables": [],
            "npm": "",
        },
        "polygon": {
            "rpc_url_template": "https://polygon-mainnet.infura.io/v3/{RPC_KEY}",
            "ws_url_template": "wss://polygon-mainnet.infura.io/ws/v3/{RPC_KEY}",
            "stables": [],
            "npm": "",
        },
        "base": {
            "rpc_url_template": "https://base-mainnet.infura.io/v3/{RPC_KEY}",
            "ws_url_template": "wss://base-mainnet.infura.io/ws/v3/{RPC_KEY}",
            "stables": [],
            "npm": "",
        },
    }