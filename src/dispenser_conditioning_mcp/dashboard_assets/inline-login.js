"use strict";
// Keep login separate from graph rendering and never persist the entered phrase.
const loginForm=document.getElementById("inline-login");
const phraseInput=document.getElementById("inline-phrase");
const loginStatus=document.getElementById("inline-login-status");
loginForm.hidden=document.body.dataset.operatorAuthorized === "true";
for(const link of document.querySelectorAll(".operator-unlock")) {
  link.addEventListener("click",event=>{
    event.preventDefault();loginForm.hidden=false;
    loginForm.scrollIntoView({behavior:"smooth",block:"center"});phraseInput.focus();
  });
}
loginForm.addEventListener("submit",async event=>{
  event.preventDefault();
  const submit=document.getElementById("inline-login-submit");
  submit.disabled=true;loginStatus.textContent="Checking access phrase…";
  const supplied=phraseInput.value;
  try {
    const response=await fetch("/dashboard/login",{method:"POST",
      headers:{"Accept":"application/json","Content-Type":"application/x-www-form-urlencoded"},
      body:new URLSearchParams({code:supplied}),signal:AbortSignal.timeout(10000)});
    if(!response.ok) {
      const message=response.status===401?"Invalid access phrase.":response.status===429?
        "Too many incorrect phrases. Try again in "+(response.headers.get("Retry-After")||"60")+" seconds.":"Login failed (HTTP "+response.status+").";
      throw new Error(message);
    }
    const result=await response.json();
    if(result.authorized!==true) throw new Error("Login was not confirmed.");
    document.body.dataset.operatorAuthorized="true";
    // Existing session polling authoritatively enables controls and loads protected
    // rows. No page navigation, cursor reset or chart-range changes are needed.
    loginForm.hidden=true;phraseInput.value="";
    const panel=document.getElementById("operator-panel");
    const heading=document.createElement("strong");heading.textContent="Dashboard access phrase";
    const value=document.createElement("div");value.id="dashboard-access-phrase";
    value.textContent=supplied.trim().toLowerCase();value.style.cssText="font-size:26px;letter-spacing:1px;margin:8px 0;user-select:all";
    const note=document.createElement("small");note.textContent="Logged in. Valid until server restart. Share only with authorized viewers.";
    panel.className="banner";panel.replaceChildren(heading,value,note);
  } catch(error) {
    loginStatus.textContent=error.name==="TimeoutError"||error.name==="AbortError"?
      "Login response timed out. Try again or wait for the next dashboard update.":error.message;
  } finally {submit.disabled=false;}
});
