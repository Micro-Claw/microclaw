Using the approach described at https://boristane.com/blog/how-i-use-claude-code/.
Every `----` indicates a new Claude session. They are kept deliberately short to
mitigate hallucination.

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

----

Please read research.md carefully. Do you see any factual 
inaccuracies? If so, please update the document to correct them.   

Thanks. Please update research.md to fix these inaccuracies.

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

I've left a comment for you in plan.md, bracketed by < and >. Please read propose changes.

----

Plan.md describes how to build an AI agent for controlling Micro-Manager via pycro-manager. Please build this agent by carefully following the plan.

Can you please add a sensible .gitignore to this repository?

----

Please carefully read the code in this repository, which describes an AI agent to control Micro-Manager (https://micro-manager.org/). When you are done, provide a short summary of what this program does and the next steps in development.

Let's focus on image analysis for adaptive acquisitions. Ideally, it should 
be possible for the agent to interpret snapped images and other information resulting
from adaptive acquisition. For example, the user should be able to ask the agent to 
automatically focus on their sample, and go through a multiposition list and focus
on each sample at each position. This could be implemented via pycro-manager 
hooks for adaptive acquistions, or by some other means. Please think deeply about 
the best approach for this. Please write a detailed plan for extending microclaw 
to be capable of adaptive acqusitions and store this plan in plan_v2.md. 

I've left some comments for you in between brackets < and > in plan_v2.md. 
Please read them carefully. If you have any questions, please ask. If not, 
please update plan_v2.md. 

Please look at plan_v2.md again. Remove anything related to Cellpose, but     
keep the architecture that would allow us to call Cellpose in the future.

----

Carefully read the code in this directory and plan_v2.md. Implement the plan
in plan_v2.md.

(Esc) Can you please provide a summary of the plan?  

Go ahead and implement it 

Now write integration tests against the Demo config

I have placed the errors I get when running the integration tests in          
errors.txt. Please have a look, read them in detail, identify root causes,    
and propose fixes. 

----

Please read the code in this repository carefully. Make a plan for how to identify unknown properties from custom        
devices. It should then be possible to call set_device_property on these previously unknown properties. Place the plan   
in a new markdown file called plan_identify_unknown_properties.md.

----

For the session described in 20260619_164417_microclaw_history.json I get the 
cProfile result in 20260619_164417_microclaw_profile.txt. What is causing     
this to run so slowly? 

Is it better to add a new run_tile_acquisiton() tool or to make it possible   
to mark multiple positions at once?

Could we wrap this into run_multiposition_acquisition instead of creating a   
separate run_tile_acquisition?  

OK. Can we do both things: extend run_multiposition_acquisition with raw      
coordinate support and add run_tile_acquisition?   

----

Please look at agent.py carefully. Is it possible for the agent to respond to 
a question and invoke multiple tool use? For example, if the users asks to    
turn on a laser and then snap an image, can the agent do both of these tasks  
using tool_use calls without making two API calls?       

Great! Can we adjust the system prompt for this?                              

----

Please look carefully at run_multiposition_acquisition() in tools.py. Is it   
possible to save the data from a multiposition acquisition if the protocol is 
set to "snap"?

Please look at                                                                
/Users/zachcm/Downloads/20260601_162424_microclaw_history_pretty.json. Here,  
the agent tried to save data after running with the protocol set to "snap".   
Why did this happen?  

Definitely, let's make sure it notifies the user if it won't save (e.g. with  
the "saved": false return). Additionally, let's make sure the function always 
saves if a file path is passed to it. If the user wants to only save one      
plane at each position, that could be done with either the zstack or          
timelapse protocols, right?      

How will the agent know to use protocol=timelapse with n_frames=1 and         
interval_s=0 if the users requests something like what is in                  
20260601_162424_microclaw_history_pretty.txt? 

----

Please look at the code carefully. Can you add tools to work with the ROI     
(region of interest) tool in Micro-Manager? 

Should set_roi and clear_roi restart live view automatically if it's 
already running, so the GUI updates immediately? → Yes, restart live if 
running

----

Look at _bounce_live_if_on in tools.py. Does it make sense to use this scheme 
for autofocus? 

Add live-mode stop/restore around the autofocus sweep                         

Do we need to add additional tests to check that the stop/start live mode     
behavior works correctly?  

----

Is it possible to make the microclaw agent load a skill when it's writing     
pycro-manager hooks? The skill could be based on the pycro-manager            
documentation (https://pycro-manager.readthedocs.io/en/latest/) and could     
help the agent write hooks.  

Is option A better than a standard Claude skill? If so, why?

The priority is on the runtime agent. Please write the implementation plan to 
runtime-pycromanager-skill.md. 

Following the updated runtime-pycromanager-skill.md, please implement the     
approach described in this file.  

----

Look at the approach to using get_hook_documentation in tools. Suppose we     
want to use a similar approach if a user askes to do SMLM or localization     
microscopy using Microclaw. Please read /Users/zachcm/Downloads/SMLM          
Primer.pdf and use the information here to write a runtime skill for          
localization microscopy. 

----

Please have a look at /Users/zachcm/Downloads/s41596-024-00989-x.pdf. Is      
there any information in this paper than can be used to augment smlm_docs.py?

Please add these now, but don't include anything specific to htSMLM in        
smlm_docs. Instead, once the smlm_docs are edited, let's have a discussion    
about creating an htSMLM/EMU docs file. 

Some setups run htSMLM/EMU. For these setups, we should load a separate skill 
that is aware of these. It may also be necessary to add some functions to     
control htSMLM/EMU, if possible. Have a look at the source code at            
https://github.com/jdeschamps/EMU and https://github.com/jdeschamps/htSMLM    
for more information. 

Auto-detect from pycro-manager, fall back to asking the user. Is there also
a way to cache this information in .microclaw for re-use once found, similar
to how hooks are saved?

Is there any check to see if EMU or htSMLM is installed before calling        
get_emu_configuration or get_htsmlm_documentation? Most Micro-Manager         
instances will not have these plugins, and we should only call these          
functions if these plugins are already installed. 

Thanks. Can you write a summary of these changes along with the design        
choices and some explicit code examples in plan-htsmlm-emu.md? 

----

Is there a way to get the pixel size of the camera from Micro-Manager? 

Thanks! Please add tests, including integration tests, for this function.

----

Look at smlm_docs.py. There is a nice biological validation benchmark there.  
Can you add DNA origami as a good reference standard for PAINT imaging? 