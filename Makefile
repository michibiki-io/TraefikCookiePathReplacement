.PHONY: default build lint test test-traefik test-release vendor clean yaegi_test

default: lint test

build:
	go build ./...

lint:
	go vet ./...

test:
	go test -race -cover ./...

test-traefik:
	python3 -B scripts/test_traefik.py

test-release:
	python3 -B -m unittest discover -s scripts/release

yaegi_test: test-traefik

vendor:
	go mod vendor

clean:
	rm -rf ./vendor
