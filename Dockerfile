FROM continuumio/anaconda3

WORKDIR /hedging/
COPY . /hedging/

RUN bash install.sh
CMD ["bash", "run.sh"]