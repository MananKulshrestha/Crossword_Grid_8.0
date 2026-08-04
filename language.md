# Translation Model Comparison for Shopping Assistant
## IndicTrans2 vs Gemma - Local Setup Guide

---

## 📋 Table of Contents
1. [Overview](#overview)
2. [System Requirements](#system-requirements)
3. [Installation Guide](#installation-guide)
4. [Complete Code](#complete-code)
5. [How to Run](#how-to-run)
6. [Understanding Results](#understanding-results)
7. [Troubleshooting](#troubleshooting)
8. [Customization Options](#customization-options)

---

## Overview

This project compares two translation models for a shopping assistant application:
- **IndicTrans2**: Specialized Indian language translation model (200M parameters)
- **Gemma**: Google's general-purpose LLM (2B parameters)

### What This Code Does
- Translates English shopping queries to Hindi
- Compares translation quality (BLEU, chrF scores)
- Measures speed (latency)
- Provides recommendations for your use case

---

## System Requirements

### Minimum Requirements
- **RAM**: 8GB minimum (16GB recommended)
- **Storage**: 5GB free space
- **OS**: Windows 10/11, macOS, or Linux
- **Python**: 3.8 or higher

### Optional but Recommended
- **GPU**: NVIDIA GPU with 4GB+ VRAM
- **CUDA**: 11.8 or higher

---

## Installation Guide

### Step 1: Clone or Download the Code

Save the code as `translation_comparison.py` or `translation_comparison.ipynb`

### Step 2: Create Virtual Environment (Recommended)

#### Windows:
```bash
python -m venv translation_env
translation_env\Scripts\activate
