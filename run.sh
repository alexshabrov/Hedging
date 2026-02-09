# https://jupyter.hedgingbt.mimir.name/?token=f477694869ffe3f4254db0a4823eea25428e83cdc6ef3ee21367df5bc3e831ee
TOKEN='f477694869ffe3f4254db0a4823eea25428e83cdc6ef3ee21367df5bc3e831ee'

while true; do
    python -m jupyter notebook --allow-root --ip=0.0.0.0 --port=8888 --notebook-dir=/hedging --NotebookApp.token=$TOKEN --NotebookApp.password=$TOKEN
    sleep 1
done