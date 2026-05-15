Using the approach described at https://boristane.com/blog/how-i-use-claude-code/

---

I want to build a Python AI agent that lets a user control Micro-Manager 
(the open-source microscopy software, micro-manager.org) through natural 
language prompts. I want to interact directly with the Java native version 
of Micro-Manager. Do not propose using pymmcore or pymmcore-plus to control
Micro-Manager. Please investigate all possible options for controlling
Micro-Manager in this way. Think deeply about which option will be 
the easiest to use for a biologist, who will not understand any code.
Write a detailed report of your research and findings in research.md.

Follow ups:

Thanks for this. Please look at research.md and re-evaluate the JPype         
approach. Even if this approach is in the style of pymmcore, that is OK. I    
just don't want to use pymmcore or pymmcore-plus, because they don't allow    
for control of the Java micromanager plugin. Please also re-evaluate the      
arXiv papers that you claim use pycro-manager. Please tell me where the       
references to pycro-manager are in these papers (which page, which line).

Please look at research.md again. It highlights headless mode as an advantage 
for biologists, because they will never have to interact with                
Micro-Manager's GUI. However, the biologists will want to see what is         
happening in the GUI in response to their requests to the LLM agent. The      
agent should, in fact, be able to interact with the GUI (click buttons,       
display the live image in Micro-Manager's camera preview window, etc.).       
Please revise the document to select the best option for a biologist, where   
the best option allows the biologist to describe their acquisition protocol   
to the agent, and then can see their acquistion protocol play out on the GUI. 
Optionally, it should also be possible to run an established aquistion       
protocol in headless mode, once the biologist has verified the pipeline works 
in the GUI.   

Have one more look at research.md. Think deeply: is it possible to use JPype  
with Micro-Manager running in standard mode (not headless)? If so, please     
update the document.

There is an unmaintained REST server for Micro-Manager, which can be used as  
an API, I think, at https://github.com/kbellve/MMrestServer. Please carefully 
check if I am correct and, if so, update the REST or HTTP API section and    
any other relevant sections of research.md. 

(new session) Please read research.md carefully. Do you see any factual 
inaccuracies? If so, please update the document to correct them.   

---

I want to build a Python AI agent that lets a user control Micro-Manager 
(the open-source microscopy software, micro-manager.org) through natural 
language prompts. The agent will use the Anthropic API with tool use, 
calling into Micro-Manager via pycro-manager. The file research.md 
describes how this can be done, so please read it carefully.

Using the information in research.md, please think deeply and create a 
detailed plan for an AI agent for micro-manager. The agent should use 
the Anthropic API with tool use. The plan should include at least the 
following:

   - How the tool definitions will map to Micro-Manager API calls
   - How the agent loop will handle multi-step acquisitions 
     (e.g. "run a z-stack then export as TIFF")
   - How hardware safety guardrails will be enforced before tool calls execute
   - How errors from Micro-Manager (e.g. device not found, stage at limit) 
     will be surfaced back to the user
   - Code snippets for implementation
   - A plan for testing the implementation:
      - Unit tests for each tool function (using Micro-Manager's Demo config 
        so no real hardware is needed)
      - Integration tests for multi-step prompts end-to-end
      - Safety boundary tests (what happens if a user asks for an out-of-range 
        stage position?)
      - Prompt-level tests: example user inputs and the expected sequence of 
        tool calls they should produce

In addition to the constraints described in research.md,
please add the following:

- It should be possible for the user to add explicit safety constraints
  to stage movement. These constraints should supersede any AI agent
  actions.
- The agent will be tested using the Micro-manager demo configuration.
  However, it should work regardless.

Please write the plan in plan.md.
