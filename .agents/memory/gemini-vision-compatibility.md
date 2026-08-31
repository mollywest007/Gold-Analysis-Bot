---
name: Gemini Vision Compatibility
description: Compatibility constraints for the bot's Google Gemini chart-analysis endpoint
---

The Gemini model used for chart-image analysis must be verified against the live Google API; retired model aliases can pass key validation while failing the actual multimodal request.

**Why:** A model-list key probe succeeded even though the chart-analysis endpoint returned HTTP 404 because its model alias had been retired.

**How to apply:** When Google changes model availability, test the complete chart-to-analysis path, not only key validity. Keep the model configurable and update the default from Google's live migration response.