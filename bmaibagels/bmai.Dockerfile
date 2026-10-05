FROM python:3-alpine
WORKDIR /app
COPY . .
COPY ./bmai-alpine ./bmai
RUN python3 -m pip install -r requirements.txt
CMD [ "python3", "./bmaibagels.py" ]
