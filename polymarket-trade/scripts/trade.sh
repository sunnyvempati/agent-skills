#!/bin/bash
# Quick trade helper — loads env and places order
# Usage: ./trade.sh buy <TOKEN_ID> <PRICE> <SIZE>
#        ./trade.sh market-buy <TOKEN_ID> <AMOUNT_USDC>
#        ./trade.sh sell <TOKEN_ID> <PRICE> <SIZE>
#        ./trade.sh balance
#        ./trade.sh book <TOKEN_ID>
#        ./trade.sh orders
#        ./trade.sh cancel-all

set -e

# Load environment
. "$HOME/.cargo/env" 2>/dev/null
export HTTPS_PROXY="$(grep HTTPS_PROXY /data/.secrets/proxy_env 2>/dev/null | cut -d'"' -f2)"

if [ -z "$HTTPS_PROXY" ]; then
    echo "ERROR: No proxy configured. Check /data/.secrets/proxy_env"
    exit 1
fi

CMD="$1"
shift

case "$CMD" in
    balance)
        polymarket clob balance --asset-type collateral -o json
        ;;
    book)
        polymarket clob book "$1"
        ;;
    orders)
        polymarket clob orders -o json
        ;;
    trades)
        polymarket clob trades -o json
        ;;
    buy)
        TOKEN="$1"; PRICE="$2"; SIZE="$3"
        echo "Buying $SIZE shares of $TOKEN at $PRICE..."
        polymarket clob create-order --token "$TOKEN" --side buy --price "$PRICE" --size "$SIZE"
        ;;
    sell)
        TOKEN="$1"; PRICE="$2"; SIZE="$3"
        echo "Selling $SIZE shares of $TOKEN at $PRICE..."
        polymarket clob create-order --token "$TOKEN" --side sell --price "$PRICE" --size "$SIZE"
        ;;
    market-buy)
        TOKEN="$1"; AMOUNT="$2"
        echo "Market buying $AMOUNT USDC of $TOKEN..."
        polymarket clob market-order --token "$TOKEN" --side buy --amount "$AMOUNT"
        ;;
    market-sell)
        TOKEN="$1"; SHARES="$2"
        echo "Market selling $SHARES shares of $TOKEN..."
        polymarket clob market-order --token "$TOKEN" --side sell --amount "$SHARES"
        ;;
    cancel-all)
        polymarket clob cancel-all
        ;;
    *)
        echo "Usage: trade.sh <command> [args]"
        echo "  balance              Check USDC balance"
        echo "  book <TOKEN>         Show orderbook"
        echo "  orders               List open orders"
        echo "  trades               List filled trades"
        echo "  buy <T> <P> <S>      Limit buy (token, price, size)"
        echo "  sell <T> <P> <S>     Limit sell"
        echo "  market-buy <T> <A>   Market buy (token, USDC amount)"
        echo "  market-sell <T> <S>  Market sell (token, shares)"
        echo "  cancel-all           Cancel all open orders"
        ;;
esac
