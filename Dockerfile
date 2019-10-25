FROM python:3.7

WORKDIR /src

RUN ls /src

ADD . /src/

RUN ls /src
RUN ls /src/quant
RUN ls /src/quant/aclient
RUN ls /src/quant/common

RUN pip install trio==0.11.0
RUN pip install -r /src/requirements.txt


ENTRYPOINT ["python", "/src/test_client.py"]