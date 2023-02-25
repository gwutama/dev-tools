#!/bin/bash

# Generate versioning.json that we put into the image later
gitinfo --gen-json

docker build --rm -t di.the-shire.utama/gwutama/bauhaus .
docker push di.the-shire.utama/gwutama/bauhaus

rm versioning.json