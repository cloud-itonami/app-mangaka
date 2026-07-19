#!/bin/bash
# Character Avatar Generator using OpenRouter API
# Usage: OPENROUTER_API_KEY=your_key ./scripts/generate-avatars.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
IMAGES_DIR="$PROJECT_ROOT/resources/images/characters"
CHARACTERS_DIR="$PROJECT_ROOT/resources/characters"

API_URL="https://openrouter.ai/api/v1/chat/completions"
MODEL="google/gemini-3-pro-image-preview"

# Check API key
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "Error: OPENROUTER_API_KEY environment variable is not set"
    echo "Usage: OPENROUTER_API_KEY=your_key ./scripts/generate-avatars.sh"
    exit 1
fi

# Ensure output directory exists
mkdir -p "$IMAGES_DIR"

# Function to generate avatar for a character
generate_avatar() {
    local slug="$1"
    local name="$2"
    local prompt="$3"

    echo ""
    echo "=== Generating avatar for $slug ($name) ==="
    echo "Prompt: ${prompt:0:100}..."

    # Prepare JSON request
    local request_body=$(cat <<EOF
{
    "model": "$MODEL",
    "messages": [{"role": "user", "content": "$prompt"}],
    "modalities": ["text", "image"],
    "image_config": {
        "aspect_ratio": "1:1",
        "image_size": "1K"
    },
    "stream": false
}
EOF
)

    # Call API
    local response=$(curl -s -X POST "$API_URL" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $OPENROUTER_API_KEY" \
        -H "HTTP-Referer: https://ghosthacker.etzhayyim.com" \
        -H "X-Title: ghosthacker-avatar-generator" \
        -d "$request_body")

    # Extract image data URL using jq
    local image_url=$(echo "$response" | jq -r '.choices[0].message.images[0].image_url.url // .choices[0].message.images[0].image_url // empty')

    if [ -z "$image_url" ] || [ "$image_url" = "null" ]; then
        echo "Error: No image in response for $slug"
        echo "Response: ${response:0:300}"
        return 1
    fi

    # Extract base64 data and save
    local base64_data=$(echo "$image_url" | sed 's/data:image\/[^;]*;base64,//')
    local output_path="$IMAGES_DIR/${slug}.png"

    echo "$base64_data" | base64 -d > "$output_path"
    echo "✓ Saved: $output_path"

    # Also save to character directory if exists
    local char_dir="$CHARACTERS_DIR/$slug"
    if [ -d "$char_dir" ]; then
        local char_avatar="$char_dir/avatar.png"
        cp "$output_path" "$char_avatar"
        echo "✓ Copied to: $char_avatar"
    fi

    return 0
}

echo "Character Avatar Generator"
echo "=========================="
echo "Output directory: $IMAGES_DIR"

# Character definitions with prompts
# Format: slug|name|prompt

declare -a CHARACTERS=(
    "Yuto|佐藤 勇人|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 16-year-old Japanese male student, expressive brown eyes, neat black hair, wearing a black school gakuran uniform, friendly but slightly anxious expression. Detailed eyes with catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Mei|桜庭 メイ|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese female student, cheerful and energetic expression, bright smile, shoulder-length black hair with a small hair clip, wearing a navy blazer school uniform. Detailed eyes with sparkling catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Saki|水野 サキ|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese female student, calm and analytical expression, sharp features, long straight black hair, wearing a neat navy blazer school uniform. Clear dark eyes with precise catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Akira|黒川 亮|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese male student, confident and smug expression, stylish features, stylishly messy black hair, wearing a navy blazer school uniform with trendy sneakers visible. Sharp dark eyes with confident catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Kota|平松 コータ|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 16-year-old Japanese male student, intellectual and curious expression, wearing glasses, short black hair slightly unkempt, wearing a school uniform. Eyes with catchlights reflecting screen glow behind glasses, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Ken|田中 健|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese male student, athletic build, tanned skin, energetic expression, short cropped black hair, wearing a navy blazer school uniform. Energetic dark eyes with bright catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Shota|山本 翔太|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese male student, quiet and thoughtful expression, slim build, slightly messy black hair, wearing a navy blazer school uniform. Quiet thoughtful eyes with soft catchlights, clean professional manga illustration, no text or speech bubbles, monochrome manga style"

    "Tsubasa|鈴木 翼|Professional manga character portrait, High-quality black and white manga illustration, Clean line art with screen tones, Shounen Jump style, Upper body portrait, 3/4 view, White background. 14-year-old Japanese male student, focused gamer expression, slightly pale skin, messy black hair with long bangs covering one eye, slouched posture, wearing a navy blazer school uniform. Focused eyes with catchlights reflecting monitor glow, clean professional manga illustration, no text or speech bubbles, monochrome manga style"
)

success_count=0
total_count=${#CHARACTERS[@]}

for char_entry in "${CHARACTERS[@]}"; do
    IFS='|' read -r slug name prompt <<< "$char_entry"

    if generate_avatar "$slug" "$name" "$prompt"; then
        ((success_count++))
    fi

    # Wait between API calls to avoid rate limiting
    if [ "$slug" != "Tsubasa" ]; then
        echo "Waiting 3 seconds before next generation..."
        sleep 3
    fi
done

echo ""
echo "=== Summary ==="
echo "Generated $success_count/$total_count avatars"
echo "Images saved to: $IMAGES_DIR"
