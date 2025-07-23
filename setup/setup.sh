#!/bin/bash

echo "🔧 Setting up Git post-commit hook..."
cp setup/post-commit.hook .git/hooks/post-commit
chmod +x .git/hooks/post-commit

if [ ! -f client_secret.json ]; then
  echo "📄 Copying shared Google OAuth client_secret..."
  cp setup/client_secret.public.json client_secret.json
fi

if [ ! -f .gdoc_image_cache.json ]; then
  echo "📄 Creating empty image cache..."
  cp setup/gdoc_image_cache.sample.json .gdoc_image_cache.json
fi

echo "✅ Setup complete."
