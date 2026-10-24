{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "62ac22b2",
   "metadata": {},
   "outputs": [
    {
     "name": "stdout",
     "output_type": "stream",
     "text": [
      "API Key Loaded: True\n"
     ]
    },
    {
     "name": "stderr",
     "output_type": "stream",
     "text": [
      "/Users/sagar_rsv/miniconda3/lib/python3.10/site-packages/langchain_google_genai/chat_models.py:3908: UserWarning: Model 'gemini-3.6-flash' uses fixed sampling defaults; the sampling parameter(s) temperature will be ignored.\n",
      "  request = self._build_request_config(\n"
     ]
    },
    {
     "name": "stdout",
     "output_type": "stream",
     "text": [
      "[{'type': 'text', 'text': '**Environment variables** are dynamic values stored within a computer\\'s operating system or execution environment that can affect the behavior of running processes. They are a fundamental tool in software development, system administration, and DevOps.\\n\\nHere are the key advantages of using environment variables:\\n\\n---\\n\\n### 1. Enhanced Security (Protection of Sensitive Data)\\n* **Keeps Secrets Out of Source Code:** Credentials like API keys, database passwords, OAuth tokens, and encryption keys should never be written directly into your code (hardcoded). \\n* **Prevents Accidental Leaks:** If your source code is pushed to a public version control system like GitHub, hardcoded secrets are exposed to the world. Using environment variables ensures that sensitive data stays on the server or developer\\'s machine, safely out of the codebase.\\n\\n---\\n\\n### 2. Separation of Code and Configuration\\n* **Adheres to Best Practices:** This aligns with the popular **Twelve-Factor App methodology**, which dictates that application configuration should be strictly separated from code.\\n* **Write Once, Run Anywhere:** Your application code remains identical across all instances. You don\\'t need to write `if (environment === \\'production\\')` checks deep inside your logic; the code simply reads the configuration provided to it at runtime.\\n\\n---\\n\\n### 3. Seamless Multi-Environment Support\\n* **Easy Context Switching:** Applications usually run in multiple environments (Development, Testing/Staging, Production). \\n* **Dynamic Behavior:** By using environment variables, you can easily change settings depending on where the application is running without altering code. For example:\\n  * **Development:** `DATABASE_URL=localhost:5432` and `DEBUG=true`\\n  * **Production:** `DATABASE_URL=db.production.com:5432` and `DEBUG=false`\\n\\n---\\n\\n### 4. Portability and Containerization (Docker/Kubernetes)\\n* **Essential for Cloud & DevOps:** Modern deployment tools like Docker, Kubernetes, AWS, Heroku, and Vercel heavily rely on environment variables.\\n* **Immutable Images:** You can build a single Docker image of your application and deploy that exact same image to staging and production. You customize its behavior simply by passing different environment variables when starting the container.\\n\\n---\\n\\n### 5. Ease of Maintenance and Deployment\\n* **No Recompilation Needed:** If a configuration setting changes (e.g., an external API endpoint updates or a database password changes), you only need to update the environment variable and restart the application. You do not need to recompile the app, rebuild binaries, or redeploy the entire codebase.\\n* **Centralized Control:** System administrators can change system-wide paths or behaviors without touching individual application files.\\n\\n---\\n\\n### 6. Granular Access Control\\n* **Role-Based Provisioning:** Production server admins can set production environment variables directly on the server. Developers can write the application code without ever needing to know the actual production database passwords or secret keys.\\n\\n---\\n\\n### Example Comparison\\n\\n❌ **Bad Approach (Hardcoded):**\\n```javascript\\n// Dangerous, inflexible, and exposes secrets in Git\\nconst dbConnection = connectToDatabase(\"mongodb://admin:SuperSecretPassword123@db.example.com\");\\nconst debugMode = true; \\n```\\n\\n✅ **Good Approach (Environment Variables):**\\n```javascript\\n// Secure, flexible, and environment-agnostic\\nconst dbConnection = connectToDatabase(process.env.DATABASE_URL);\\nconst debugMode = process.env.NODE_ENV === \\'development\\';\\n```\\n\\n---\\n\\n### Summary of Best Practices\\nWhile environment variables offer massive advantages, maximize their benefits by:\\n* Never committing `.env` files containing real secrets to Git (add `.env` to your `.gitignore`).\\n* Providing a `.env.example` file that lists the *names* of required variables (with dummy values) to help team members set up their local environments.', 'extras': {'signature': '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'}}]\n"
     ]
    }
   ],
   "source": [
    "from langchain_core.messages import BaseMessage\n",
    "from langgraph.graph import StateGraph, START, END\n",
    "from dotenv import load_dotenv\n",
    "from langgraph.checkpoint.memory import InMemorySaver\n",
    "from typing import Annotated, TypedDict, List, Optional\n",
    "from langchain_google_genai import ChatGoogleGenerativeAI\n",
    "\n",
    "load_dotenv()  # Load environment variables from .env file\n",
    "\n",
    "import os\n",
    "from pathlib import Path\n",
    "from dotenv import load_dotenv\n",
    "\n",
    "# Find the .env file in the current working directory\n",
    "env_path = Path('.') / '.env'\n",
    "load_dotenv(dotenv_path=env_path)\n",
    "\n",
    "# Verify it loaded despite the terminal warning\n",
    "print(\"API Key Loaded:\", \"GROQ_API_KEY\" in os.environ)\n",
    "\n",
    "llm = ChatGoogleGenerativeAI(\n",
    "    model=\"gemini-3.6-flash\"\n",
    ")\n",
    "\n",
    "response = llm.invoke(\"What are the advantages of using environment variables?\")\n",
    "print(response.content)"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": 9,
   "id": "5fe9e2a2",
   "metadata": {},
   "outputs": [
    {
     "name": "stdout",
     "output_type": "stream",
     "text": [
      "Collecting langchain-google-genai\n",
      "  Downloading langchain_google_genai-4.4.0-py3-none-any.whl (81 kB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m81.6/81.6 kB\u001b[0m \u001b[31m4.5 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0m\n",
      "\u001b[?25hCollecting google-genai<3.0.0,>=2.20.0\n",
      "  Downloading google_genai-2.23.0-py3-none-any.whl (1.1 MB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m1.1/1.1 MB\u001b[0m \u001b[31m15.0 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0ma \u001b[36m0:00:01\u001b[0m\n",
      "\u001b[?25hCollecting filetype<2.0.0,>=1.2.0\n",
      "  Downloading filetype-1.2.0-py2.py3-none-any.whl (19 kB)\n",
      "Requirement already satisfied: langchain-core<2.0.0,>=1.6.1 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-google-genai) (1.6.3)\n",
      "Requirement already satisfied: pydantic<3.0.0,>=2.0.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-google-genai) (2.13.5)\n",
      "Requirement already satisfied: requests<3.0.0,>=2.28.1 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (2.28.1)\n",
      "Requirement already satisfied: distro<2,>=1.7.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.9.0)\n",
      "Requirement already satisfied: sniffio in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.3.1)\n",
      "Collecting google-auth[requests]<3.0.0,>=2.56.0\n",
      "  Downloading google_auth-2.58.0-py3-none-any.whl (262 kB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m262.4/262.4 kB\u001b[0m \u001b[31m12.8 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0m\n",
      "\u001b[?25hRequirement already satisfied: websockets<17.0,>=13.0.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (16.1.1)\n",
      "Requirement already satisfied: httpx<1.0.0,>=0.28.1 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (0.28.1)\n",
      "Requirement already satisfied: tenacity<9.2.0,>=8.2.3 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (9.1.4)\n",
      "Requirement already satisfied: typing-extensions<5.0.0,>=4.14.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (4.16.0)\n",
      "Requirement already satisfied: anyio<5.0.0,>=4.8.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-genai<3.0.0,>=2.20.0->langchain-google-genai) (4.15.1)\n",
      "Requirement already satisfied: pyyaml<7.0.0,>=5.3.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (6.0.3)\n",
      "Requirement already satisfied: packaging>=23.2.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (26.3)\n",
      "Requirement already satisfied: langchain-protocol>=0.0.17 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (0.0.19)\n",
      "Requirement already satisfied: langsmith<1.0.0,>=0.3.45 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (0.12.4)\n",
      "Requirement already satisfied: jsonpatch<2.0.0,>=1.33.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (1.33)\n",
      "Requirement already satisfied: uuid-utils<1.0,>=0.12.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (0.17.1)\n",
      "Requirement already satisfied: pydantic-core==2.46.5 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from pydantic<3.0.0,>=2.0.0->langchain-google-genai) (2.46.5)\n",
      "Requirement already satisfied: annotated-types>=0.6.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from pydantic<3.0.0,>=2.0.0->langchain-google-genai) (0.8.0)\n",
      "Requirement already satisfied: typing-inspection>=0.4.2 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from pydantic<3.0.0,>=2.0.0->langchain-google-genai) (0.4.4)\n",
      "Requirement already satisfied: idna>=2.8 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from anyio<5.0.0,>=4.8.0->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (3.19)\n",
      "Requirement already satisfied: exceptiongroup>=1.0.2 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from anyio<5.0.0,>=4.8.0->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.3.1)\n",
      "Collecting pyasn1-modules>=0.2.1\n",
      "  Downloading pyasn1_modules-0.4.2-py3-none-any.whl (181 kB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m181.3/181.3 kB\u001b[0m \u001b[31m14.1 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0m\n",
      "\u001b[?25hRequirement already satisfied: cryptography>=38.0.3 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from google-auth[requests]<3.0.0,>=2.56.0->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (39.0.1)\n",
      "Collecting requests<3.0.0,>=2.28.1\n",
      "  Downloading requests-2.34.2-py3-none-any.whl (73 kB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m73.1/73.1 kB\u001b[0m \u001b[31m10.3 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0m\n",
      "\u001b[?25hRequirement already satisfied: httpcore==1.* in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from httpx<1.0.0,>=0.28.1->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.0.9)\n",
      "Requirement already satisfied: certifi in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from httpx<1.0.0,>=0.28.1->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (2023.5.7)\n",
      "Requirement already satisfied: h11>=0.16 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from httpcore==1.*->httpx<1.0.0,>=0.28.1->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (0.16.0)\n",
      "Requirement already satisfied: jsonpointer>=1.9 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from jsonpatch<2.0.0,>=1.33.0->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (2.1)\n",
      "Requirement already satisfied: requests-toolbelt>=1.0.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (1.0.0)\n",
      "Requirement already satisfied: httpx2<3,>=2 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (2.12.0)\n",
      "Requirement already satisfied: zstandard>=0.23.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (0.25.0)\n",
      "Requirement already satisfied: orjson>=3.9.14 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (3.12.0)\n",
      "Requirement already satisfied: xxhash>=3.0.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (4.0.1)\n",
      "Requirement already satisfied: charset_normalizer<4,>=2 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from requests<3.0.0,>=2.28.1->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (2.0.4)\n",
      "Requirement already satisfied: urllib3<3,>=1.26 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from requests<3.0.0,>=2.28.1->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.26.15)\n",
      "Requirement already satisfied: cffi>=1.12 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from cryptography>=38.0.3->google-auth[requests]<3.0.0,>=2.56.0->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (1.15.1)\n",
      "Requirement already satisfied: httpcore2==2.12.0 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from httpx2<3,>=2->langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (2.12.0)\n",
      "Requirement already satisfied: truststore>=0.10 in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from httpx2<3,>=2->langsmith<1.0.0,>=0.3.45->langchain-core<2.0.0,>=1.6.1->langchain-google-genai) (0.10.4)\n",
      "Collecting pyasn1<0.7.0,>=0.6.1\n",
      "  Downloading pyasn1-0.6.4-py3-none-any.whl (84 kB)\n",
      "\u001b[2K     \u001b[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\u001b[0m \u001b[32m84.4/84.4 kB\u001b[0m \u001b[31m8.2 MB/s\u001b[0m eta \u001b[36m0:00:00\u001b[0m\n",
      "\u001b[?25hRequirement already satisfied: pycparser in /Users/sagar_rsv/miniconda3/lib/python3.10/site-packages (from cffi>=1.12->cryptography>=38.0.3->google-auth[requests]<3.0.0,>=2.56.0->google-genai<3.0.0,>=2.20.0->langchain-google-genai) (2.21)\n",
      "Installing collected packages: filetype, requests, pyasn1, pyasn1-modules, google-auth, google-genai, langchain-google-genai\n",
      "  Attempting uninstall: requests\n",
      "    Found existing installation: requests 2.28.1\n",
      "    Uninstalling requests-2.28.1:\n",
      "      Successfully uninstalled requests-2.28.1\n",
      "Successfully installed filetype-1.2.0 google-auth-2.58.0 google-genai-2.23.0 langchain-google-genai-4.4.0 pyasn1-0.6.4 pyasn1-modules-0.4.2 requests-2.34.2\n",
      "Note: you may need to restart the kernel to use updated packages.\n"
     ]
    }
   ],
   "source": [
    "pip install langchain-google-genai"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "394bca2f",
   "metadata": {},
   "outputs": [],
   "source": []
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "3f22bd56",
   "metadata": {},
   "outputs": [],
   "source": []
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "a8ce8238",
   "metadata": {},
   "outputs": [],
   "source": []
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "id": "fd81da2f",
   "metadata": {},
   "outputs": [],
   "source": []
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "base",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "mimetype": "text/x-python",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.10.10"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 5
}
