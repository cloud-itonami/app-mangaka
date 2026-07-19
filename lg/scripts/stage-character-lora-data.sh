#!/bin/bash
# Stage LoRA training data on the Windows ComfyUI host.
# Layout (kohya convention): <data_path>/<num>_<trigger>/img.png + img.txt
#
# We use num=10 (10 repeats per epoch) and the trigger token = the
# character's lowercase slug (yuto_persona / ren_persona / nei_persona).
# Caption per image = a tag-style description derived from the variant
# filename (e.g., "anxious_front" → "anxious expression, front view, ...")

set -euo pipefail

CHARS_LOCAL="/Users/junkawasaki/etzhayyim/etzhayyim-root/60-apps/etzhayyim-project-mangaka/data/ghosthacker/resources/characters"
REMOTE_BASE='/c:/Users/gad/lora-data'

# Variant filename → caption fragment
declare -A VARIANT_CAPTION=(
    [action_shout]="dynamic action, mouth open shouting, intense expression"
    [angry_3q_left]="angry expression, three-quarter view, looking left"
    [anxious_front]="anxious expression, front view"
    [downcast_sad]="sad downcast expression, looking down"
    [focused_3q_right]="focused expression, three-quarter view, looking right"
    [gentle_smile_3q]="gentle smile, three-quarter view"
    [neutral_front]="neutral expression, front view"
    [profile_left]="profile view, looking left"
    [profile_right]="profile view, looking right"
    [shocked_wide]="shocked expression, wide-open eyes"
    [thinking_3q]="thinking expression, hand near chin, three-quarter view"
    [back_pose]="back view"
    [closed_eyes_serene]="serene expression, closed eyes"
)

for CHAR in Yuto Ren Nei; do
    SLUG=$(echo "$CHAR" | tr '[:upper:]' '[:lower:]')
    TRIGGER="${SLUG}_persona"
    DEST_DIR="lora-data-${SLUG}/10_${TRIGGER}"
    LOCAL_STAGE="/tmp/${DEST_DIR}"
    mkdir -p "$LOCAL_STAGE"

    # Read character profile for base caption
    PROFILE="$CHARS_LOCAL/$CHAR/profile.jsonld"
    BASE_TAGS=$(python3 -c "
import json
p = json.load(open('$PROFILE'))
a = p.get('gh:appearance', {})
gender = '1boy' if 'male' in a.get('gh:face','').lower() else '1girl'
tags = [
    'masterpiece, best quality, anime illustration',
    '$TRIGGER',
    gender,
    f'{p.get(\"schema:age\", 17)} year old',
    a.get('gh:hair',''),
    a.get('gh:eyes',''),
    a.get('gh:build',''),
]
print(', '.join([t for t in tags if t]))
")

    echo "=== $CHAR ($TRIGGER) ==="
    echo "  base: $BASE_TAGS"

    # Copy + caption each variant
    for IMG in "$CHARS_LOCAL/$CHAR/reference_variants/"*.png; do
        BASENAME=$(basename "$IMG" .png)
        cp "$IMG" "$LOCAL_STAGE/${BASENAME}.png"
        # Build caption: base + variant
        VARIANT_CAP="${VARIANT_CAPTION[$BASENAME]:-character variant}"
        echo "${BASE_TAGS}, ${VARIANT_CAP}" > "$LOCAL_STAGE/${BASENAME}.txt"
    done

    # Also include main reference image
    cp "$CHARS_LOCAL/$CHAR/reference.png" "$LOCAL_STAGE/reference.png"
    echo "${BASE_TAGS}, neutral pose, character reference" > "$LOCAL_STAGE/reference.txt"

    COUNT=$(ls "$LOCAL_STAGE"/*.png | wc -l | tr -d ' ')
    echo "  staged: $COUNT images locally at $LOCAL_STAGE"
done

# Upload to Windows host
echo
echo "=== Uploading to ComfyUI host ==="
ssh gad@192.168.1.70 'powershell -Command "New-Item -ItemType Directory -Path C:\Users\gad\lora-data-yuto -Force; New-Item -ItemType Directory -Path C:\Users\gad\lora-data-ren -Force; New-Item -ItemType Directory -Path C:\Users\gad\lora-data-nei -Force" -EA SilentlyContinue'

for CHAR in yuto ren nei; do
    LOCAL_PARENT="/tmp/lora-data-${CHAR}"
    REMOTE_PATH="/c:/Users/gad/lora-data-${CHAR}/"
    echo "  scp -r ${LOCAL_PARENT}/* gad@192.168.1.70:${REMOTE_PATH}"
    scp -r "${LOCAL_PARENT}"/* "gad@192.168.1.70:${REMOTE_PATH}" 2>&1 | tail -3
done

echo
echo "=== Verify on Windows host ==="
ssh gad@192.168.1.70 'powershell -NoProfile -Command "
foreach (\$c in @(\"yuto\",\"ren\",\"nei\")) {
    \$d = \"C:\Users\gad\lora-data-\$c\10_${c}_persona\"
    if (Test-Path \$d) {
        \$n = (Get-ChildItem \$d -File -Filter *.png).Count
        Write-Host \"  \$c: \$d has \$n images\"
    } else {
        Write-Host \"  \$c: MISSING \$d\"
    }
}"' 2>&1 | tail -10
