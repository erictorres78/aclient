# aclient

build:
    
    docker build -t test_aclient .
    
run:

    docker run --rm -it -e ID=ID00000001 -e URL=http://localhost:8087 --net host test_aclient

