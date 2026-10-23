import os
import re
import sys
from pathlib import Path

from openai import AzureOpenAI

endpoint = "https://app-uoaihack2ix6ak.azurewebsites.net"
model_name = "gpt-6-astra"

api_key = os.environ["OPENAI_API_KEY"]
api_version = "2024-12-01-preview"

output_dir = Path("build")

client = AzureOpenAI(
    api_version=api_version,
    azure_endpoint=endpoint,
    api_key=api_key,
)

system_prompt = (Path(__file__).parent / "system_prompt.md").read_text()

# Usage: python main.py "describe what you want built"
task = " ".join(sys.argv[1:]) or input("What should Astra build? ")

response = client.chat.completions.create(
    messages=[
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": task,
        },
    ],
    max_completion_tokens=32768,
    model=model_name,
    stream=True,
)

# Stream the response to the terminal as it is written
content = ""
for chunk in response:
    if chunk.choices and chunk.choices[0].delta.content:
        content += chunk.choices[0].delta.content
        print(chunk.choices[0].delta.content, end="", flush=True)
print("\n")

files = re.findall(r'<file path="([^"]+)">\n?(.*?)</file>', content, flags=re.DOTALL)
for path, body in files:
    target = (output_dir / path).resolve()
    # Refuse paths that would escape the output directory
    if not target.is_relative_to(output_dir.resolve()):
        print(f"skipped unsafe path: {path}")
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    print(f"wrote {output_dir / path}")

if not files:
    print("No files found in the response.")
