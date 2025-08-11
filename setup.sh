cat > setup.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "🔧 Installing Python dependencies…"
python3 -m pip install --upgrade pip
pip install -r requirements.txt

echo "🧩 Installing Playwright Chromium (with system deps)…"
# Install the browser assets used by Playwright (needed on Streamlit Cloud)
python3 -m playwright install chromium --with-deps

echo "✅ Setup complete."
EOF
chmod +x setup.sh
