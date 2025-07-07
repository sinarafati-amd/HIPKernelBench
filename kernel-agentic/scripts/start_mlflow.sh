#!/bin/bash

# Navigate to the root directory of the project
cd ..

# Create the mlflow-artifacts bucket in MinIO if it doesn't exist
echo "Setting up MinIO bucket..."
docker run --rm --network host \
  -e MINIO_ROOT_USER=minioaccesskey \
  -e MINIO_ROOT_PASSWORD=miniosecretkey \
  minio/mc:latest \
  sh -c "mc config host add myminio http://localhost:9000 minioaccesskey miniosecretkey && \
         mc mb --ignore-existing myminio/mlflow-artifacts"

# Start the Docker Compose services
echo "Starting MLflow services..."
docker-compose up -d

echo "MLflow server is running at http://localhost:5000"
echo "MinIO console is available at http://localhost:9000"
