# ============================================================================
# TRANSLATION COMPARISON: IndicTrans2 vs Gemma
# For Shopping Assistant Project - LOCAL VERSION
# ============================================================================

# ============================================================================
# PART 1: INSTALLATION (Run this first in terminal)
# ============================================================================
"""
First, install required packages in terminal:

# Create virtual environment (optional but recommended)
python -m venv translation_env
source translation_env/bin/activate  # On Windows: translation_env\Scripts\activate

# Install packages
pip install torch transformers datasets sacrebleu sentencepiece accelerate bitsandbytes
pip install git+https://github.com/AI4Bharat/IndicTransToolkit.git
pip install matplotlib pandas numpy
pip install "transformers<5.0.0"

# For GPU support (optional)
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
"""

# ============================================================================
# PART 2: IMPORTS
# ============================================================================

import torch
import time
import pandas as pd
import numpy as np
import os
from transformers import (
    AutoTokenizer, 
    AutoModelForSeq2SeqLM,
    AutoModelForCausalLM,
    BitsAndBytesConfig
)
from IndicTransToolkit import IndicProcessor
import warnings
warnings.filterwarnings('ignore')

# Check GPU
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Using device: {device}")
if device == "cuda":
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"GPU Memory: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
else:
    print("⚠️ No GPU detected. Running on CPU (will be slower)")

# ============================================================================
# PART 3: LOAD INDICTRANS2 (Translation-focused model)
# ============================================================================

print("\n=== Loading IndicTrans2 (Distilled 200M) ===\n")

try:
    INDIC_MODEL = "ai4bharat/indictrans2-indic-en-dist-200M"
    
    # Load tokenizer and model
    indic_tokenizer = AutoTokenizer.from_pretrained(
        INDIC_MODEL, 
        trust_remote_code=True
    )
    indic_model = AutoModelForSeq2SeqLM.from_pretrained(
        INDIC_MODEL,
        trust_remote_code=True,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
    ).to(device)
    indic_model.eval()
    
    # Initialize IndicProcessor for preprocessing
    indic_processor = IndicProcessor(inference=True)
    
    print(f"✅ IndicTrans2 loaded successfully!")
    print(f"   Model: {INDIC_MODEL}")
    print(f"   Parameters: {sum(p.numel() for p in indic_model.parameters()) / 1e6:.1f}M")
    
except Exception as e:
    print(f"❌ Failed to load IndicTrans2: {e}")
    print("   Try running: pip install git+https://github.com/AI4Bharat/IndicTransToolkit.git")
    indic_model = None
    indic_tokenizer = None
    indic_processor = None

# ============================================================================
# PART 4: LOAD GEMMA (General-purpose LLM)
# ============================================================================

print("\n=== Loading Gemma (2B) ===\n")

try:
    GEMMA_MODEL = "google/gemma-2b"
    
    # Configure 4-bit quantization to save memory
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4"
    )
    
    # Load tokenizer and model
    gemma_tokenizer = AutoTokenizer.from_pretrained(GEMMA_MODEL)
    gemma_model = AutoModelForCausalLM.from_pretrained(
        GEMMA_MODEL,
        quantization_config=bnb_config,
        device_map="auto" if device == "cuda" else None,
        torch_dtype=torch.float16,
    )
    if device == "cpu":
        gemma_model = gemma_model.to("cpu")
    gemma_model.eval()
    
    # Set padding token
    gemma_tokenizer.pad_token = gemma_tokenizer.eos_token
    
    print(f"✅ Gemma loaded successfully!")
    print(f"   Model: {GEMMA_MODEL}")
    print(f"   Parameters: {sum(p.numel() for p in gemma_model.parameters()) / 1e9:.1f}B")
    
except Exception as e:
    print(f"❌ Failed to load Gemma: {e}")
    print("   Note: Gemma requires accepting terms at https://huggingface.co/google/gemma-2b")
    print("   And setting up authentication: huggingface-cli login")
    gemma_model = None
    gemma_tokenizer = None

print("\n✅ Model loading complete!\n")

# ============================================================================
# PART 5: TRANSLATION FUNCTIONS
# ============================================================================

# Language mapping for IndicTrans2
LANG_MAP = {
    'en': 'eng_Latn',
    'hi': 'hin_Deva',
    'bn': 'ben_Beng',
    'te': 'tel_Telu',
    'ta': 'tam_Taml',
    'ml': 'mal_Mlym',
    'kn': 'kan_Knda',
    'gu': 'guj_Gujr',
    'mr': 'mar_Deva',
    'pa': 'pan_Guru',
}

def translate_indic(text, src_lang, tgt_lang):
    """Translate using IndicTrans2"""
    if indic_model is None or indic_tokenizer is None:
        return "[IndicTrans2 not available]"
    
    try:
        # Map language codes
        src = LANG_MAP.get(src_lang, src_lang)
        tgt = LANG_MAP.get(tgt_lang, tgt_lang)
        
        # Preprocess
        batch = indic_processor.preprocess_batch(
            [text],
            src_lang=src,
            tgt_lang=tgt,
        )
        
        # Tokenize
        inputs = indic_tokenizer(
            batch,
            truncation=True,
            padding="longest",
            return_tensors="pt",
            return_attention_mask=True,
        ).to(device)
        
        # Generate translation
        with torch.no_grad():
            generated_tokens = indic_model.generate(
                **inputs,
                use_cache=True,
                min_length=0,
                max_length=256,
                num_beams=5,
                num_return_sequences=1,
            )
        
        # Decode
        translation = indic_tokenizer.batch_decode(
            generated_tokens,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )[0]
        
        # Postprocess
        translation = indic_processor.postprocess_batch(
            [translation], 
            lang=tgt
        )[0]
        
        return translation
        
    except Exception as e:
        return f"[IndicTrans2 Error: {str(e)}]"

def translate_gemma(text, src_lang, tgt_lang):
    """Translate using Gemma"""
    if gemma_model is None or gemma_tokenizer is None:
        return "[Gemma not available]"
    
    try:
        # Create prompt for translation
        prompt = f"""Translate the following {src_lang} text to {tgt_lang}. Provide only the translation, no explanations.

{src_lang}: {text}
{tgt_lang}:"""
        
        # Tokenize
        inputs = gemma_tokenizer(
            prompt, 
            return_tensors="pt",
            truncation=True,
            max_length=512
        )
        
        # Move to device if not already there
        if device == "cuda":
            inputs = {k: v.to(gemma_model.device) for k, v in inputs.items()}
        
        # Generate
        with torch.no_grad():
            outputs = gemma_model.generate(
                **inputs,
                max_new_tokens=256,
                temperature=0.3,
                do_sample=True,
                pad_token_id=gemma_tokenizer.eos_token_id,
            )
        
        # Decode
        full_response = gemma_tokenizer.decode(outputs[0], skip_special_tokens=True)
        
        # Extract only the translation part
        translation = full_response.split(f"{tgt_lang}:")[-1].strip()
        
        # If nothing found, return the full response
        if not translation:
            translation = full_response
        
        return translation
        
    except Exception as e:
        return f"[Gemma Error: {str(e)}]"

def translate_mixed(text, src_lang, tgt_lang):
    """Mixed approach: IndicTrans2 for speed, refine with Gemma for complex text"""
    try:
        # Primary translation with IndicTrans2
        primary = translate_indic(text, src_lang, tgt_lang)
        
        # If text is complex and Gemma is available, refine
        if gemma_model and len(text.split()) > 15:
            prompt = f"""Refine this translation. Improve accuracy and naturalness.

Original: {text}
Current: {primary}
Refined:"""
            
            inputs = gemma_tokenizer(
                prompt, 
                return_tensors="pt",
                truncation=True,
                max_length=512
            )
            
            if device == "cuda":
                inputs = {k: v.to(gemma_model.device) for k, v in inputs.items()}
            
            with torch.no_grad():
                outputs = gemma_model.generate(
                    **inputs,
                    max_new_tokens=256,
                    temperature=0.3,
                    do_sample=True,
                    pad_token_id=gemma_tokenizer.eos_token_id,
                )
            
            refined = gemma_tokenizer.decode(outputs[0], skip_special_tokens=True)
            refined = refined.split("Refined:")[-1].strip()
            
            if refined:
                return refined
        
        return primary
        
    except Exception as e:
        return f"[Mixed Error: {str(e)}]"

# Dictionary of models (only include working models)
MODELS = {}

if indic_model is not None:
    MODELS['IndicTrans2'] = translate_indic

if gemma_model is not None:
    MODELS['Gemma'] = translate_gemma

if indic_model is not None and gemma_model is not None:
    MODELS['Mixed (Indic+Gemma)'] = translate_mixed

if not MODELS:
    print("❌ No models loaded! Please check installation.")
    exit()

print(f"✅ Loaded {len(MODELS)} models: {', '.join(MODELS.keys())}\n")

# ============================================================================
# PART 6: CREATE TEST DATASET (Shopping domain)
# ============================================================================

print("=== Creating Shopping Test Dataset ===\n")

test_data = pd.DataFrame([
    # Basic shopping queries
    {"src_text": "What is the price of this product?", "tgt_text": "Is product ki kya keemat hai?", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "I want to buy a red dress", "tgt_text": "Mujhe ek laal dress khareedni hai", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "The delivery will take 3 days", "tgt_text": "Delivery ko 3 din lagenge", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "Do you have this in blue color?", "tgt_text": "Kya aapke paas ye neele rang mein hai?", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "Can I return this item?", "tgt_text": "Kya main is item ko wapas kar sakta hoon?", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "How much does shipping cost?", "tgt_text": "Shipping ki keemat kya hai?", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "Is this available in size M?", "tgt_text": "Kya ye size M mein available hai?", "src_lang": "en", "tgt_lang": "hi"},
    # Complex queries
    {"src_text": "Could you please tell me how much this would cost including shipping and handling fees?", "tgt_text": "Kya aap mujhe bata sakte hain ki shipping aur handling fees samet iski keemat kya hogi?", "src_lang": "en", "tgt_lang": "hi"},
    {"src_text": "I need to order five items but I'm not sure if they will arrive before the weekend", "tgt_text": "Mujhe paanch items order karne hain lekin mujhe yakeen nahi hai ki ve weekend se pehle aa jaayenge", "src_lang": "en", "tgt_lang": "hi"},
])

print(f"✅ Test dataset created with {len(test_data)} sentences")
print(test_data[['src_text', 'tgt_text']].head())

# ============================================================================
# PART 7: RUN EVALUATION
# ============================================================================

print("\n=== Running Evaluation ===\n")

results = []

for idx, row in test_data.iterrows():
    src_text = row['src_text']
    ref_text = row['tgt_text']
    src_lang = row['src_lang']
    tgt_lang = row['tgt_lang']
    
    print(f"\n[{idx+1}] Source: {src_text}")
    print(f"    Reference: {ref_text}")
    
    for model_name, translate_func in MODELS.items():
        try:
            start = time.time()
            translation = translate_func(src_text, src_lang, tgt_lang)
            latency = time.time() - start
            
            results.append({
                'model': model_name,
                'source': src_text,
                'reference': ref_text,
                'translation': translation,
                'latency': latency
            })
            
            print(f"    {model_name:20s}: {translation[:60]}... ({latency:.2f}s)")
            
        except Exception as e:
            print(f"    {model_name:20s}: ERROR - {str(e)}")

results_df = pd.DataFrame(results)
print(f"\n✅ Evaluation complete! {len(results_df)} translations")

# ============================================================================
# PART 8: CALCULATE METRICS
# ============================================================================

print("\n=== Calculating Metrics ===\n")

import sacrebleu

metrics = []

for model_name in results_df['model'].unique():
    model_results = results_df[results_df['model'] == model_name]
    
    translations = model_results['translation'].tolist()
    references = model_results['reference'].tolist()
    
    # Filter out errors
    valid_translations = []
    valid_references = []
    for t, r in zip(translations, references):
        if not t.startswith('[') and t != '':
            valid_translations.append(t)
            valid_references.append(r)
    
    if valid_translations:
        try:
            bleu = sacrebleu.corpus_bleu(valid_translations, [valid_references])
            chrf = sacrebleu.corpus_chrf(valid_translations, [valid_references])
        except:
            bleu = sacrebleu.BLEU(0)
            chrf = sacrebleu.CHRF(0)
        
        avg_latency = model_results['latency'].mean()
        
        metrics.append({
            'model': model_name,
            'BLEU': round(bleu.score, 2) if hasattr(bleu, 'score') else 0,
            'chrF': round(chrf.score, 2) if hasattr(chrf, 'score') else 0,
            'Latency (s)': round(avg_latency, 3),
            'Valid': len(valid_translations),
            'Total': len(model_results)
        })

metrics_df = pd.DataFrame(metrics)
print("✅ Metrics calculated!")
print(metrics_df.to_string(index=False))

# ============================================================================
# PART 9: VISUALIZATION
# ============================================================================

print("\n=== Results Visualization ===\n")

try:
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.use('Agg')  # For non-interactive environments
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    
    # BLEU Scores
    ax1 = axes[0]
    ax1.bar(metrics_df['model'], metrics_df['BLEU'], color='skyblue')
    ax1.set_title('BLEU Scores')
    ax1.set_ylabel('BLEU Score')
    ax1.set_ylim(0, max(metrics_df['BLEU']) * 1.2 + 5 if not metrics_df.empty else 10)
    for i, v in enumerate(metrics_df['BLEU']):
        ax1.text(i, v + 1, str(v), ha='center')
    
    # chrF Scores
    ax2 = axes[1]
    ax2.bar(metrics_df['model'], metrics_df['chrF'], color='lightgreen')
    ax2.set_title('chrF Scores')
    ax2.set_ylabel('chrF Score')
    ax2.set_ylim(0, max(metrics_df['chrF']) * 1.2 + 5 if not metrics_df.empty else 10)
    for i, v in enumerate(metrics_df['chrF']):
        ax2.text(i, v + 1, str(v), ha='center')
    
    # Latency
    ax3 = axes[2]
    ax3.bar(metrics_df['model'], metrics_df['Latency (s)'], color='salmon')
    ax3.set_title('Average Latency')
    ax3.set_ylabel('Seconds')
    for i, v in enumerate(metrics_df['Latency (s)']):
        ax3.text(i, v + 0.05, f'{v:.2f}s', ha='center')
    
    plt.tight_layout()
    plt.savefig('translation_comparison.png', dpi=200, bbox_inches='tight')
    plt.close()
    print("✅ Visualization saved as 'translation_comparison.png'")
    
except Exception as e:
    print(f"⚠️ Visualization error: {e}")

# ============================================================================
# PART 10: RECOMMENDATIONS
# ============================================================================

print("\n" + "="*60)
print("FINAL RECOMMENDATIONS")
print("="*60)

if not metrics_df.empty:
    best_bleu = metrics_df.loc[metrics_df['BLEU'].idxmax()]
    best_chrf = metrics_df.loc[metrics_df['chrF'].idxmax()]
    fastest = metrics_df.loc[metrics_df['Latency (s)'].idxmin()]
    
    print(f"\n📊 Summary:")
    print(f"  • Best Translation Quality (BLEU): {best_bleu['model']} ({best_bleu['BLEU']})")
    print(f"  • Best Translation Quality (chrF): {best_chrf['model']} ({best_chrf['chrF']})")
    print(f"  • Fastest Model: {fastest['model']} ({fastest['Latency (s)']}s)")
    
    print(f"\n💡 Recommendation for Shopping Assistant:")
    if best_bleu['model'] == 'IndicTrans2':
        print("  ✅ USE INDICTRANS2")
        print("  • Best translation quality for Indian languages")
        print("  • Fast and efficient (200M parameters)")
        print("  • Free and open-source")
        print("  • Optimized specifically for Indian language translation")
    elif best_bleu['model'] == 'Gemma':
        print("  ✅ USE GEMMA")
        print("  • Good for general understanding and complex queries")
        print("  • Can handle context better")
        print("  • Requires more resources (2B parameters)")
    elif best_bleu['model'] == 'Mixed (Indic+Gemma)':
        print("  ✅ USE MIXED APPROACH")
        print("  • Best of both worlds")
        print("  • Use IndicTrans2 for speed, refine with Gemma for complex queries")
else:
    print("  ⚠️ Not enough data for recommendations")

print("\n" + "="*60)
print("BENCHMARK COMPLETE! 🎉")
print("="*60)

# ============================================================================
# PART 11: SAVE RESULTS
# ============================================================================

# Save to local directory
results_df.to_csv('translation_results.csv', index=False)
metrics_df.to_csv('metrics_summary.csv', index=False)

print("\n✅ Results saved locally:")
print("  • translation_results.csv - All translations")
print("  • metrics_summary.csv - Performance metrics")
print("  • translation_comparison.png - Visualization")

# ============================================================================
# PART 12: LOAD FLORES-200 DATASET (Optional)
# ============================================================================

def load_flores_dataset():
    """Load FLORES-200 dataset for better evaluation"""
    try:
        from datasets import load_dataset
        
        print("\n=== Loading FLORES-200 Dataset ===\n")
        
        # Load English-Hindi subset
        dataset = load_dataset("facebook/flores", "en-hi", split="devtest")
        
        # Convert to DataFrame
        flores_df = pd.DataFrame({
            'src_text': dataset['sentence_en'][:50],  # First 50 sentences
            'tgt_text': dataset['sentence_hi'][:50],
            'src_lang': ['en'] * 50,
            'tgt_lang': ['hi'] * 50
        })
        
        print(f"✅ FLORES-200 dataset loaded with {len(flores_df)} sentences")
        return flores_df
        
    except Exception as e:
        print(f"⚠️ Could not load FLORES-200: {e}")
        print("   Install with: pip install datasets")
        return None

# Uncomment to use FLORES-200 instead of custom dataset:
# flores_data = load_flores_dataset()
# if flores_data is not None:
#     test_data = flores_data

# ============================================================================
# PART 13: TEST A SINGLE SENTENCE (Optional)
# ============================================================================

def test_sentence(text, src="en", tgt="hi"):
    """Test all models on a single sentence"""
    print(f"\n{'='*60}")
    print(f"Testing: {text}")
    print(f"{'='*60}\n")
    
    for name, func in MODELS.items():
        start = time.time()
        translation = func(text, src, tgt)
        latency = time.time() - start
        print(f"{name:20s}: {translation}")
        print(f"{' ' * 20} (took {latency:.2f}s)")
        print()

# Uncomment to test:
# test_sentence("What is the return policy for this product?")
# test_sentence("I need to buy a gift for my mother's birthday")

print("\n🎉 Ready! To test a custom sentence, use: test_sentence('your text here')")