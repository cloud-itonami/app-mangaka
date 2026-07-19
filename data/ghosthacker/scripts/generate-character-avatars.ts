/**
 * Character Avatar Generator Script
 * Generates manga-style avatar images for characters using OpenRouter API (Gemini 3 Pro)
 *
 * Usage:
 *   OPENROUTER_API_KEY=your_key npx tsx scripts/generate-character-avatars.ts
 */

import * as fs from 'fs';
import * as path from 'path';

const OPENROUTER_API_KEY = process.env.OPENROUTER_API_KEY || '';
const OPENROUTER_API_URL = 'https://openrouter.ai/api/v1/chat/completions';
const MODEL = 'google/gemini-3-pro-image-preview';

interface CharacterProfile {
  '@id': string;
  'gh:slug': string;
  'schema:name': string;
  'schema:age': number;
  'schema:role': string;
  'gh:appearance': {
    'gh:generationPrompt'?: string;
    'gh:face'?: string;
    'gh:hair'?: string;
    'gh:eyes'?: string;
    'gh:build'?: string;
    'gh:style'?: string;
  };
  'gh:alias'?: string;
}

// Characters to generate avatars for
const CHARACTER_SLUGS = [
  'Yuto',
  'Mei',
  'Saki',
  'Akira',
  'Kota',
  'Ken',
  'Shota',
  'Tsubasa',
];

async function loadCharacterProfile(slug: string): Promise<CharacterProfile | null> {
  const profilePath = path.join(__dirname, '../resources/characters', slug, 'profile.jsonld');

  try {
    const content = fs.readFileSync(profilePath, 'utf-8');
    return JSON.parse(content) as CharacterProfile;
  } catch (error) {
    console.error(`Failed to load profile for ${slug}:`, error);
    return null;
  }
}

function buildAvatarPrompt(profile: CharacterProfile): string {
  const appearance = profile['gh:appearance'];

  // Build manga-style avatar prompt
  const parts: string[] = [
    'Professional manga character portrait',
    'High-quality black and white manga illustration',
    'Clean line art with screen tones',
    'Shounen Jump style',
    'Upper body portrait, 3/4 view',
    'White background',
  ];

  // Add character-specific appearance
  if (appearance['gh:face']) {
    parts.push(appearance['gh:face']);
  }

  if (appearance['gh:hair']) {
    parts.push(appearance['gh:hair']);
  }

  if (appearance['gh:eyes']) {
    parts.push(appearance['gh:eyes']);
  }

  // Add age and role context
  const name = profile['schema:name'];
  const age = profile['schema:age'];
  const role = profile['schema:role'];

  parts.push(`${age}-year-old Japanese student`);
  parts.push(`Character role: ${role}`);

  // Style suffix
  parts.push('Monochrome manga style');
  parts.push('Detailed eyes with catchlights');
  parts.push('Clean professional manga illustration');
  parts.push('No text or speech bubbles');

  return parts.join('. ');
}

async function generateImage(prompt: string): Promise<string | null> {
  if (!OPENROUTER_API_KEY) {
    console.error('OPENROUTER_API_KEY is not set');
    return null;
  }

  console.log('Generating image with prompt:', prompt.substring(0, 100) + '...');

  try {
    const response = await fetch(OPENROUTER_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${OPENROUTER_API_KEY}`,
        'HTTP-Referer': 'https://ghosthacker.etzhayyim.com',
        'X-Title': 'ghosthacker-character-avatar-generator',
      },
      body: JSON.stringify({
        model: MODEL,
        messages: [{ role: 'user', content: prompt }],
        modalities: ['text', 'image'],
        image_config: {
          aspect_ratio: '1:1',
          image_size: '1K', // OpenRouter accepts: "1K", "2K", "4K"
        },
        stream: false,
      }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      console.error('OpenRouter API error:', response.status, errorData);
      return null;
    }

    const result = await response.json() as any;

    // Extract image data URL
    const imageUrl =
      result?.choices?.[0]?.message?.images?.[0]?.image_url?.url ??
      result?.choices?.[0]?.message?.images?.[0]?.image_url ??
      null;

    if (!imageUrl || typeof imageUrl !== 'string') {
      console.error('No image in response:', JSON.stringify(result).substring(0, 200));
      return null;
    }

    return imageUrl;
  } catch (error) {
    console.error('Error generating image:', error);
    return null;
  }
}

function saveImage(dataUrl: string, outputPath: string): boolean {
  try {
    // Extract base64 data from data URL
    const parts = dataUrl.split(',');
    if (parts.length !== 2) {
      console.error('Invalid data URL format');
      return false;
    }

    const base64Data = parts[1];
    const imageBuffer = Buffer.from(base64Data, 'base64');

    // Ensure directory exists
    const dir = path.dirname(outputPath);
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    fs.writeFileSync(outputPath, imageBuffer);
    console.log(`Saved image to: ${outputPath}`);
    return true;
  } catch (error) {
    console.error('Error saving image:', error);
    return false;
  }
}

async function generateCharacterAvatar(slug: string): Promise<boolean> {
  console.log(`\n=== Generating avatar for ${slug} ===`);

  // Load profile
  const profile = await loadCharacterProfile(slug);
  if (!profile) {
    console.error(`Could not load profile for ${slug}`);
    return false;
  }

  console.log(`Character: ${profile['schema:name']}`);

  // Build prompt
  const prompt = buildAvatarPrompt(profile);

  // Generate image
  const imageDataUrl = await generateImage(prompt);
  if (!imageDataUrl) {
    console.error(`Failed to generate image for ${slug}`);
    return false;
  }

  // Save to characters images directory
  const outputPath = path.join(__dirname, '../resources/images/characters', `${slug}.png`);
  const saved = saveImage(imageDataUrl, outputPath);

  if (saved) {
    // Also save to character's own directory
    const charDir = path.join(__dirname, '../resources/characters', slug);
    if (fs.existsSync(charDir)) {
      const charAvatarPath = path.join(charDir, 'avatar.png');
      saveImage(imageDataUrl, charAvatarPath);
    }
  }

  return saved;
}

async function main() {
  console.log('Character Avatar Generator');
  console.log('==========================');

  if (!OPENROUTER_API_KEY) {
    console.error('\nError: OPENROUTER_API_KEY environment variable is not set');
    console.error('Usage: OPENROUTER_API_KEY=your_key npx tsx scripts/generate-character-avatars.ts');
    process.exit(1);
  }

  const results: { slug: string; success: boolean }[] = [];

  for (const slug of CHARACTER_SLUGS) {
    const success = await generateCharacterAvatar(slug);
    results.push({ slug, success });

    // Add delay between API calls to avoid rate limiting
    if (CHARACTER_SLUGS.indexOf(slug) < CHARACTER_SLUGS.length - 1) {
      console.log('Waiting 2 seconds before next generation...');
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
  }

  // Summary
  console.log('\n=== Summary ===');
  for (const { slug, success } of results) {
    console.log(`${success ? '✓' : '✗'} ${slug}`);
  }

  const successCount = results.filter(r => r.success).length;
  console.log(`\nGenerated ${successCount}/${results.length} avatars`);
}

main().catch(console.error);
